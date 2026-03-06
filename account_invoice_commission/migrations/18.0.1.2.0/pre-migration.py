import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)


@openupgrade.migrate()
def migrate(env, version):
    cr = env.cr

    _logger.info("START add date_last_payment to account_move")
    openupgrade.add_columns(env, [
        ("account_move", "date_last_payment", "date"),
    ])
    _logger.info("END add date_last_payment to account_move")