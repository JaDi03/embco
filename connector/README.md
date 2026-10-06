# connector

Scripts that prepare an ERPNext instance for the embco agent. They run inside Frappe
(`bench console`), not in the agent process.

| File | What it does |
|---|---|
| `prereqs.py` | Adds the wallet field on Supplier, the transaction hash, payee wallet and decision fields on Payment Entry, and turns on the purchase order and receipt requirements. Safe to run twice |

Copy this folder to `/home/frappe/connector` in the ERPNext container, then:

```python
from connector.prereqs import apply; apply()
```

The API user the agent needs is created separately with the roles Purchase User, Stock User
and Accounts User. It never needs System Manager.
