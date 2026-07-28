{
    "name": "Pyroxide Ledger Audit",
    "version": "19.0.1.0.0",
    "summary": "Request-local ledger audit using a byte-only Pyroxide boundary",
    "category": "Accounting/Accounting",
    "author": "Pyroxide",
    "license": "LGPL-3",
    "depends": ["account"],
    "data": [
        "security/ir.model.access.csv",
        "views/audit_run_views.xml",
    ],
    "external_dependencies": {"python": ["pyroxide"]},
    "installable": True,
    "application": False,
    "auto_install": False,
}
