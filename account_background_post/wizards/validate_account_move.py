import logging

from odoo import _, fields, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ValidateAccountMove(models.TransientModel):
    _inherit = "validate.account.move"

    count_inv = fields.Integer(help="Technical field to know the number of invoices selected from the wizard")
    batch_size = fields.Integer(compute="_compute_batch_size")
    force_background = fields.Integer(compute="_compute_force_background")
    use_background_post = fields.Boolean(compute="_compute_use_background_post")

    def _compute_batch_size(self):
        self.batch_size = int(self.env["ir.config_parameter"].sudo().get_param("account_background_post.batch_size", 20))

    def _compute_force_background(self):
        for rec in self:
            rec.force_background = rec.count_inv > rec.batch_size

    def _compute_use_background_post(self):
        for rec in self:
            rec.use_background_post = bool(rec.move_ids) and all(
                move.country_code == "AR" and move.move_type in ("out_invoice", "out_refund")
                for move in rec.move_ids
            )

    def default_get(self, fields):
        res = super().default_get(fields)
        move_ids = res.get("move_ids", [])
        if move_ids and move_ids[0][0] == 6:
            res["count_inv"] = len(move_ids[0][2])
        return res

    def action_background_post(self):
        moves = self.move_ids.filtered(
            lambda move: move.state == "draft"
            and move.country_code == "AR"
            and move.move_type in ("out_invoice", "out_refund")
        )
        moves.background_post = True
        if moves:
            self.env.ref("account_background_post.ir_cron_background_post_invoices")._trigger()
        return {"type": "ir.actions.act_window_close"}

    def validate_move(self):
        background_moves = self.move_ids.filtered(
            lambda move: move.country_code == "AR" and move.move_type in ("out_invoice", "out_refund")
        )
        if self.move_ids - background_moves:
            return super().validate_move()
        if background_moves and len(background_moves) > self.batch_size:
            raise UserError(
                _(
                    "You can only validate batches smaller than %s invoices. Use background posting for larger batches.",
                    self.batch_size,
                )
            )
        for move in background_moves:
            _logger.info("Validating invoice %s", move.id)
            move.action_post()
            move.env.cr.commit()
        return {"type": "ir.actions.act_window_close"}

    def validate_move_confirm(self):
        return self.validate_move()
