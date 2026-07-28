try:
    from . import models as models
except ModuleNotFoundError as error:
    if error.name != "odoo":
        raise
