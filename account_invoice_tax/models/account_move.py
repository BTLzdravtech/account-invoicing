from contextlib import contextmanager

from odoo import fields, models


class AccountMove(models.Model):
    _inherit = "account.move"

    # Stores manually-overridden tax amounts keyed by tax id (as string).
    # Structure: { "<tax_id>": {"amount": float, "rate": float} }
    # Only fixed-amount taxes are stored here; percentage taxes are always
    # recomputed automatically.
    tax_override_data = fields.Json()

    def sync_tax_override_from_tax_totals(self):
        for rec in self.filtered(
            lambda move: move.country_code == "AR"
            and move.move_type in ("in_invoice", "in_refund", "in_receipt")
        ):
            tax_totals = rec.tax_totals
            if not tax_totals:
                continue

            is_company_currency = rec.currency_id == rec.company_currency_id
            new_overrides = {}

            for subtotal in tax_totals.get("subtotals", []):
                for tax_group in subtotal.get("tax_groups", []):
                    tax_amount = tax_group.get("tax_amount", 0.0)
                    tax_amount_currency = tax_group.get("tax_amount_currency", 0.0)

                    for tax_id in tax_group.get("involved_tax_ids", []):
                        tax = rec.env["account.tax"].browse(tax_id)
                        if not tax.exists() or tax.amount_type != "fixed":
                            continue

                        if is_company_currency:
                            amount = tax_amount
                            rate = 1
                        else:
                            amount = tax_amount_currency
                            rate = rec.invoice_currency_rate or 1.0

                        new_overrides[str(tax_id)] = {
                            "amount": amount,
                            "rate": rate,
                        }

            rec.tax_override_data = new_overrides or False

    # ------------------------------------------------------------------
    # Tax-totals widget: inject override amounts so the widget reflects
    # the manually-set values instead of the recomputed ones.
    # ------------------------------------------------------------------

    def _compute_tax_totals(self):
        for move in self:
            overrides = move.tax_override_data or {}
            if move.country_code != "AR" or not overrides:
                super(AccountMove, move)._compute_tax_totals()
                continue

            tax_context = {
                "tax_context": {
                    int(tax_id): {
                        "fixed_amount": vals["amount"],
                        "rate": move.invoice_currency_rate or 1.0,
                    }
                    for tax_id, vals in overrides.items()
                },
            }
            super(AccountMove, move.with_context(**tax_context))._compute_tax_totals()
            move.tax_totals["account_invoice_tax_readonly"] = True

    # ------------------------------------------------------------------
    # Re-apply overrides after every tax-line recomputation so that the
    # amounts set through the wizard survive any subsequent edits on the
    # invoice lines.
    # ------------------------------------------------------------------

    @contextmanager
    def _sync_tax_lines(self, container):
        records = container.get("records", self).filtered(lambda move: move.country_code == "AR")
        if not records:
            with super()._sync_tax_lines(container):
                yield
            return

        _before_state = {
            move: {
                "rate": move.invoice_currency_rate,
                "taxed_base_ids": frozenset(
                    move.line_ids.filtered(lambda l: l.display_type == "product" and l.tax_ids).ids
                ),
            }
            for move in records
            if move.id and move.state == "draft"
        }

        with super()._sync_tax_lines(container):
            yield

        AccountTax = self.env["account.tax"]
        for move in records:
            if move.state == "draft" and move.id:
                before = _before_state.get(move)
                if before and before["rate"] != move.invoice_currency_rate:
                    current_taxed_base_ids = frozenset(
                        move.line_ids.filtered(lambda l: l.display_type == "product" and l.tax_ids).ids
                    )
                    if before["taxed_base_ids"] - current_taxed_base_ids:
                        # Force a fresh recompute from base lines.
                        base_lines_values, tax_lines_values = move._get_rounded_base_and_tax_lines(
                            round_from_tax_lines=False
                        )
                        AccountTax._add_accounting_data_in_base_lines_tax_details(
                            base_lines_values,
                            move.company_id,
                            include_caba_tags=move.always_tax_exigible,
                        )
                        tax_results = AccountTax._prepare_tax_lines(
                            base_lines_values,
                            move.company_id,
                            tax_lines=tax_lines_values,
                        )
                        for _tax_line_vals, _grouping_key, to_update in tax_results["tax_lines_to_update"]:
                            _tax_line_vals["record"].write(dict(to_update))
                        if tax_results.get("tax_lines_to_delete"):
                            self.env["account.move.line"].browse(
                                v["record"].id for v in tax_results["tax_lines_to_delete"]
                            ).with_context(dynamic_unlink=True).unlink()
                        if tax_results.get("tax_lines_to_add"):
                            self.env["account.move.line"].create(
                                [
                                    {**vals, "display_type": "tax", "move_id": move.id}
                                    for vals in tax_results["tax_lines_to_add"]
                                ]
                            )
            move._apply_tax_overrides()

    def _apply_tax_overrides(self, other_taxes_override=False):
        """Re-write values from ``tax_override_data`` onto the matching tax lines.

        Only overrides for fixed-amount taxes are applied; percentage-based
        taxes are always recomputed unless they are present in the
        ``other_taxes_override`` dictionary.
        """
        overrides = self.tax_override_data or {}
        if other_taxes_override:
            # Percentage-based tax overrides are not stored in ``tax_override_data``
            # as they don't need to survive recomputations, but we still want to
            # apply them on the current tax lines.
            overrides.update(other_taxes_override)
        if not overrides or self.state == "posted":
            return

        move_currency = self.currency_id
        company_currency = self.company_currency_id
        not_company_currency = move_currency and move_currency != company_currency
        # Para in_invoice/in_receipt el impuesto positivo va al débito; para
        # in_refund/out_* va al crédito. El signo se aplica una sola vez sobre
        # el amount ingresado por el usuario (que puede ser negativo) y de ahí
        # se derivan debit, credit y balance manteniendo la consistencia
        # contable (debit - credit == balance).
        sign = 1 if self.move_type in ("in_invoice", "in_receipt") else -1
        for line in self.line_ids.filtered(lambda l: l.tax_line_id and str(l.tax_line_id.id) in overrides):
            vals = overrides[str(line.tax_line_id.id)]
            rate = line.move_id.invoice_currency_rate or 1.0
            amount = vals.get("amount", 0.0)
            amount_cc = amount / rate
            signed_amount = amount * sign
            signed_amount_cc = amount_cc * sign

            line_vals = {
                "debit": max(signed_amount_cc, 0.0) if not_company_currency else max(signed_amount, 0.0),
                "credit": max(-signed_amount_cc, 0.0) if not_company_currency else max(-signed_amount, 0.0),
                "balance": signed_amount_cc if not_company_currency else signed_amount,
            }
            if not_company_currency and amount:
                line_vals["amount_currency"] = signed_amount
            line.write(line_vals)
