from __future__ import annotations

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from app.components.formatting import CHECK_SOURCE, CHECK_STATE, ist
from app.components.widgets import Banner, Card, KeyValueGrid, Page, Pill, button, muted
from models.readiness import CheckState
from services.container import AppContext
from services.readiness_service import MANUAL_ITEMS
from services.wallet_readiness_service import WalletState


class PreparationView(Page):
    def __init__(self, ctx: AppContext):
        super().__init__("Booking preparation",
                         "Work through the checklist before opening time. Items you confirm yourself are labelled "
                         "'Confirmed by you'; TatkalMate cannot see IRCTC and never treats them as verified.")
        self.ctx = ctx
        self.banner = Banner()
        self.content.addWidget(self.banner)

        self.check_card = Card("Readiness checklist")
        self.check_holder = QWidget()
        self.check_grid = QGridLayout(self.check_holder)
        self.check_grid.setContentsMargins(0, 0, 0, 0)
        self.check_grid.setHorizontalSpacing(14)
        self.check_grid.setVerticalSpacing(10)
        self.check_card.body.addWidget(self.check_holder)
        self.content.addWidget(self.check_card)

        wallet = Card("IRCTC eWallet readiness")
        self.wallet_pill = Pill()
        wallet.header.addWidget(self.wallet_pill)
        self.wallet_grid = KeyValueGrid(["Status", "Last checked", "Recommended balance"])
        wallet.body.addWidget(self.wallet_grid)
        form = QFormLayout()
        self.fare = QDoubleSpinBox()
        self.fare.setRange(0, 100000)
        self.fare.setDecimals(0)
        self.fare.setPrefix("₹ ")
        self.fare.setSpecialValueText("Not set")
        self.fare.setMaximumWidth(220)
        self.fare.setToolTip("Your estimate of the total fare for all passengers. Used only to suggest a balance.")
        form.addRow("Estimated total fare", self.fare)
        wallet.body.addLayout(form)
        row = QHBoxLayout()
        self.open_irctc = button("Open IRCTC in Brave", primary=True)
        self.mark_wallet = button("I've checked my balance")
        self.clear_wallet = button("Reset")
        row.addWidget(self.open_irctc)
        row.addWidget(self.mark_wallet)
        row.addWidget(self.clear_wallet)
        row.addStretch(1)
        wallet.body.addLayout(row)
        wallet.body.addWidget(muted("TatkalMate never reads your wallet balance and never touches payment. "
                                    "Check the balance yourself on the official IRCTC site."))
        self.content.addWidget(wallet)

        steps = Card("How eWallet payment works on IRCTC")
        for i, text in enumerate(ctx.wallet.payment_steps, 1):
            l = QLabel(f"{i}.  {text}")
            l.setWordWrap(True)
            steps.body.addWidget(l)
        self.content.addWidget(steps)
        self.content.addStretch(1)

        self.fare.editingFinished.connect(self._save_fare)
        self.mark_wallet.clicked.connect(self.ctx.wallet.mark_checked)
        self.clear_wallet.clicked.connect(self.ctx.wallet.clear)
        self.open_irctc.clicked.connect(self._open_irctc)
        self.refresh()

    def handle_event(self, e) -> None:
        self.refresh()

    def refresh(self) -> None:
        while self.check_grid.count():
            w = self.check_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        for r, it in enumerate(self.ctx.readiness.items()):
            text, tone = CHECK_STATE[it.state]
            pill = Pill(text, tone)
            pill.setFixedSize(84, 22)
            self.check_grid.addWidget(pill, r, 0, Qt.AlignmentFlag.AlignVCenter)
            box = QVBoxLayout()
            name = QLabel(f"<b>{it.label}</b>")
            box.addWidget(name)
            box.addWidget(muted(f"{CHECK_SOURCE[it.source]} · {it.detail}"
                                + (f" · {ist(it.confirmed_at, '%d %b %H:%M')}" if it.confirmed_at else "")))
            holder = QWidget()
            holder.setLayout(box)
            box.setContentsMargins(0, 0, 0, 0)
            self.check_grid.addWidget(holder, r, 1)
            b = None
            if it.id in MANUAL_ITEMS:
                b = button("Undo" if it.state is CheckState.DONE else "Confirm")
                action = self.ctx.readiness.unconfirm if it.state is CheckState.DONE else self.ctx.readiness.confirm
                b.clicked.connect(lambda _=False, i=it.id, a=action: a(i))
            elif it.id == "wallet_checked":
                b = button("Undo" if it.state is CheckState.DONE else "Confirm")
                b.clicked.connect(self.ctx.wallet.clear if it.state is CheckState.DONE else self.ctx.wallet.mark_checked)
            if b is not None:
                b.setFixedWidth(90)
                self.check_grid.addWidget(b, r, 2)
        self.check_grid.setColumnStretch(1, 1)

        st = self.ctx.wallet.status()
        tone = {WalletState.CHECKED: "info", WalletState.EXPIRED: "warn", WalletState.NOT_CHECKED: "warn"}[st.state]
        self.wallet_pill.set({WalletState.CHECKED: "Checked by you", WalletState.EXPIRED: "Check again",
                              WalletState.NOT_CHECKED: "Not checked"}[st.state], tone)
        self.wallet_grid.set("Status", st.message)
        self.wallet_grid.set("Last checked", ist(st.checked_at, "%d %b %Y, %H:%M"))
        self.wallet_grid.set("Recommended balance", f"₹{st.recommended_balance:,} (fare + 10% buffer)"
                             if st.recommended_balance else "Enter an estimated fare to get a suggestion")
        if not self.fare.hasFocus():
            self.fare.setValue(st.estimated_fare or 0)

    def _save_fare(self) -> None:
        v = self.fare.value()
        self.ctx.wallet.set_estimated_fare(v if v > 0 else None)

    def _open_irctc(self) -> None:
        url = self.ctx.config.config.browser.irctc_url
        try:
            self.ctx.brave.launch(url)
            self.banner.set("Brave opened with the IRCTC site. Log in there yourself.", "ok")
        except Exception as exc:
            QDesktopServices.openUrl(QUrl(url))
            self.banner.set(f"{exc} Opened in your default browser instead.", "warn")
