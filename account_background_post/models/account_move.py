import logging

from odoo import _, api, fields, models
from odoo.tools import plaintext2html

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    background_post = fields.Boolean(
        help="If True then this invoice will be validated in the background by cron.", copy=False, tracking=True
    )

    def get_internal_partners(self):
        res = self.env["res.partner"]
        for partner in self.message_partner_ids:
            if partner.user_ids and all(user._is_internal() for user in partner.user_ids):
                res |= partner
        return res

    @api.model
    def _cron_background_post_invoices(self, ids=None):
        """Post invoices queued for background processing."""
        if ids is not None:
            moves = self.browse(ids)
        else:
            moves = self.search(
                [
                    ("background_post", "=", True),
                    ("state", "=", "draft"),
                    ("country_code", "=", "AR"),
                    ("move_type", "in", ("out_invoice", "out_refund")),
                ]
            )
        moves = moves.filtered(
            lambda move: move.country_code == "AR" and move.move_type in ("out_invoice", "out_refund")
        )

        total_len = len(moves)
        self.env["ir.cron"]._commit_progress(remaining=total_len)
        for move in moves:
            try:
                move.action_post()
                self.env["ir.cron"]._commit_progress(processed=1)
            except Exception as exp:
                self.env.cr.rollback()
                move.background_post = False
                move.message_post(
                    body=_("We tried to validate this invoice on the background but got this error")
                    + ": \n\n"
                    + plaintext2html(str(exp), "em"),
                    partner_ids=move.get_internal_partners().ids,
                    body_is_html=True,
                )
                _logger.error("Error while trying to post invoice %s in background: %s", move.name, exp)
                # Commit after each failure to set false background_post and post the message
                self.env.cr.commit()  # pylint: disable=invalid-commit

    def _post(self, soft=True):
        to_defer = self.env["account.move"]
        if self.env.context.get("force_background_post"):
            to_defer = self.filtered(
                lambda move: move.country_code == "AR" and move.move_type in ("out_invoice", "out_refund")
            )
            to_defer.background_post = True
        posted = super(AccountMove, self - to_defer)._post(soft=soft)
        posted.filtered("background_post").background_post = False
        return posted
