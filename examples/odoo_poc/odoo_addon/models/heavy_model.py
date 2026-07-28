import base64
import hashlib

from odoo import fields, models
from pyroxide import task


@task
def checksum_payload(payload: bytes) -> bytes:
    """Example request-local work; this is not a durable Odoo job."""
    return hashlib.sha256(payload).hexdigest().encode("ascii")


class HeavyModel(models.Model):
    _name = "pyroxide.heavy_model"
    _description = "Pyroxide Request-Local Task Demo"

    name = fields.Char(string="Task Name", required=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("processing", "Processing"),
            ("done", "Done"),
            ("failed", "Failed"),
        ],
        default="draft",
        string="Status",
    )
    result_data = fields.Binary(string="Processed Result")

    def action_process(self):
        """Submit work and wait inside the current Odoo worker process.

        Pyroxide task handles and IDs are process-local. Do not persist them as
        durable Odoo jobs. Use Odoo's queue infrastructure when work must survive
        worker restarts or run on another host.
        """
        for record in self:
            record.state = "processing"
            try:
                payload = record.name.encode("utf-8")
                result = checksum_payload(payload).result(timeout_sec=10)
            except Exception:
                record.state = "failed"
                raise
            record.result_data = base64.b64encode(result)
            record.state = "done"
        return True
