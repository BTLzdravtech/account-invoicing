##############################################################################
# For copyright and license notices, see __manifest__.py file in module root
# directory
##############################################################################
import logging

from odoo import api, fields, models

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    commission_amount = fields.Monetary(
        compute="_compute_commission_amount",
        currency_field="company_currency_id",
    )
    commission_invoice_ids = fields.Many2many(
        "account.move",
        "account_invoice_commission_inv_rel",
        "commissioned_id",
        "commission_id",
        string="Commission Invoices",
        domain=[("move_type", "in", ("in_invoice", "in_refund"))],
        readonly=True,
        help="Commision invoices where this invoice is commissioned",
        copy=False,
    )
    commissioned_invoice_ids = fields.Many2many(
        "account.move",
        "account_invoice_commission_inv_rel",
        "commission_id",
        "commissioned_id",
        # 'commission_invoice_id',
        domain=[("move_type", "in", ("out_invoice", "out_refund"))],
        string="Commissioned invoices",
        readonly=True,
        help="The invoices that this commission invoice is commissioning",
        copy=False,
    )
    # este campo lo agregamos aca y no en account_usability para no perjudicar
    # a quien no usa comisiones en temas de performance
    date_last_payment = fields.Date(
        compute="_compute_date_last_payment",
        string="Last Payment Date",
        store=True,
    )
    partner_user_id = fields.Many2one(
        "res.users",
        compute="_compute_partner_user",
    )

    @api.depends("partner_id.user_ids")
    def _compute_partner_user(self):
        for rec in self:
            users = rec.partner_id.user_ids
            rec.partner_user_id = users and users[0] or False

    # amount_residual already tracks payment reconciliation changes.
    @api.depends("amount_residual")
    def _compute_date_last_payment(self):
        for rec in self:
            rec.date_last_payment = False
            if rec.move_type != "entry" and rec.state == "posted":
                payments = rec._get_reconciled_payments()
                rec.date_last_payment = payments and payments[-1].date

    @api.depends("invoice_line_ids.commission_amount")
    @api.depends_context("commissioned_partner_id")
    def _compute_commission_amount(self):
        commissioned_partner_id = self.env.context.get("commissioned_partner_id")
        if commissioned_partner_id:
            _logger.info("Computing commission amount")
            for rec in self:
                rec.commission_amount = sum(rec.mapped("invoice_line_ids.commission_amount"))
        else:
            self.commission_amount = 0.0

    def web_read(self, specification):
        """Propagate the commissioned partner through nested reads."""
        res = super().web_read(specification)
        for vals, rec in zip(res, self):
            partner_id = vals.get("partner_id")
            if (
                rec.country_code == "AR"
                and partner_id
                and isinstance(partner_id, dict)
                and partner_id.get("id")
                and "commissioned_invoice_ids" in specification
            ):
                vals["commissioned_invoice_ids"] = (
                    super(
                        AccountMove,
                        rec.with_context(commissioned_partner_id=vals["partner_id"]["id"]),
                    ).web_read({"commissioned_invoice_ids": specification["commissioned_invoice_ids"]})[0][
                        "commissioned_invoice_ids"
                    ]
                )
        return res

    def _fetch_duplicate_reference(self, matching_states=("draft", "posted")):
        duplicates = super()._fetch_duplicate_reference(matching_states=matching_states)
        bypass_moves = self.filtered(
            lambda move: move.country_code == "AR"
            and (move.commissioned_invoice_ids or move.commission_invoice_ids)
        )
        for move in bypass_moves:
            duplicates[move] = self.env["account.move"]
        return duplicates
