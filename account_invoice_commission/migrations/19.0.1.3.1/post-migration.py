from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    last_id = 0
    while moves := env["account.move"].search([("id", ">", last_id)], order="id", limit=10000):
        env.add_to_compute(moves._fields["date_last_payment"], moves)
        moves._recompute_recordset(["date_last_payment"])
        last_id = moves[-1].id
