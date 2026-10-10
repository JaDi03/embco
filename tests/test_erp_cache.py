"""A check reads only what changed in the ERP, and decides exactly as if it had read it all."""

from decimal import Decimal

from agent.guardrails.rules import DecisionEngine, PolicyConfig
from services.erp.erpnext.cache import CachedLedger
from support import FakeLedger, make_invoice

POLICY = PolicyConfig(max_per_payment=Decimal(5000), weekly_budget=Decimal(9000))


class CountingErp(FakeLedger):
    """The ERP as the cache sees it: a light index with change stamps, and full reads counted."""

    def __init__(self):
        super().__init__()
        self.pending = [make_invoice("PINV-1", "B-1"), make_invoice("PINV-2", "B-2")]
        self.modified = {"PINV-0": "t0", "PINV-1": "t0", "PINV-2": "t0", "PO-1": "t0",
                         "PR-1": "t0"}
        self.reads = []
        self.supplier_reads = 0

    def invoice_index(self):
        rows = []
        for inv in [*self.history, *self.pending]:
            rows.append({"name": inv.name, "modified": self.modified[inv.name],
                         "supplier": inv.supplier, "due_date": inv.due_date.isoformat(),
                         "outstanding_amount": str(inv.outstanding_amount)})
        return rows

    def stamps(self, doctype):
        names = self.orders if doctype == "Purchase Order" else self.receipts
        return {n: self.modified[n] for n in names}

    def get_purchase_invoice(self, name):
        self.reads.append(name)
        return super().get_purchase_invoice(name)

    def get_purchase_order(self, name):
        self.reads.append(name)
        return super().get_purchase_order(name)

    def get_purchase_receipt(self, name):
        self.reads.append(name)
        return super().get_purchase_receipt(name)

    def get_supplier(self, name):
        self.supplier_reads += 1
        return super().get_supplier(name)


def check(ledger):
    if isinstance(ledger, CachedLedger):
        ledger.refresh()
    return [(d.invoice, d.action, d.reasons) for d in DecisionEngine(ledger, POLICY).decide_all()]


def test_a_second_check_with_nothing_changed_downloads_nothing_and_decides_the_same():
    erp = CountingErp()
    cached = CachedLedger(erp)
    first = check(cached)
    assert first == check(erp)  # the same decisions as reading it all
    erp.reads.clear()
    assert check(cached) == first
    assert erp.reads == []


def test_only_the_document_that_changed_is_read_again():
    erp = CountingErp()
    cached = CachedLedger(erp)
    check(cached)
    erp.reads.clear()
    erp.modified["PINV-2"] = "t1"
    check(cached)
    assert erp.reads == ["PINV-2"]


def test_the_amount_due_comes_from_the_fresh_list_even_without_a_new_version():
    erp = CountingErp()
    cached = CachedLedger(erp)
    check(cached)
    erp.pending = [erp.pending[0], erp.pending[1].model_copy(update={"outstanding_amount":
                                                                     Decimal(0)})]
    assert [i.name for i in (cached.refresh() or cached.list_unpaid_purchase_invoices())] == [
        "PINV-1"]


def test_the_supplier_and_its_wallet_are_always_read_live():
    erp = CountingErp()
    cached = CachedLedger(erp)
    check(cached)
    before = erp.supplier_reads
    check(cached)
    assert erp.supplier_reads > before


def test_every_few_checks_everything_is_read_again():
    erp = CountingErp()
    cached = CachedLedger(erp, full_every=2)
    check(cached)
    check(cached)
    erp.reads.clear()
    check(cached)  # the third check is a full one again
    assert set(erp.reads) >= {"PINV-1", "PINV-2", "PO-1", "PR-1"}
