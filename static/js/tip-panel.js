// Tip panel preset amounts (VERB-110 panel, VERB-112 mount).
//
// Progressive enhancement only: clicking a preset writes its whole-CHF value
// into the amount_chf number input and takes the pressed state. The input
// itself lives in the panel's <details> and is a fully working control on its
// own, so nothing here is load-bearing.
//
// A file rather than an inline <script> because the panel's real mount is the
// confirmed-match page, inside the HTMX-swapped #match-actions block. htmx
// re-creates script nodes it swaps in, and a re-created inline script loses
// its CSP nonce, so it is blocked under the production policy (script-src
// 'self' 'nonce-...') — and silently allowed by the report-only policy in
// development, which hides the breakage. A same-origin src is covered by
// 'self' at both mounts.
//
// The handlers are delegated from document and registered once, so
// re-executing this file after a later swap is a no-op.
(function () {
    if (window.__tipPanelPresetsBound) {
        return;
    }
    window.__tipPanelPresetsBound = true;

    // Selection is carried on aria-pressed, not a class: the chips are a
    // radio-like group, so the state has to reach assistive tech and not just
    // the stylesheet, which keys its pressed styling off the same attribute.
    function setPressed(panel, selected) {
        panel.querySelectorAll(".tip-chip").forEach(function (chip) {
            chip.setAttribute("aria-pressed", chip === selected ? "true" : "false");
        });
    }

    document.addEventListener("click", function (event) {
        var target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        var chip = target.closest(".tip-chip");
        if (!chip) {
            return;
        }
        var panel = chip.closest("#tip-panel");
        var amountInput =
            panel && panel.querySelector('input[name="amount_chf"]');
        if (!amountInput) {
            return;
        }
        amountInput.value = chip.getAttribute("data-tip-amount");
        setPressed(panel, chip);
    });

    // Typing a free amount clears the chips — leaving one lit while the input
    // says something else would misreport what is about to be submitted.
    document.addEventListener("input", function (event) {
        var target = event.target;
        if (!(target instanceof Element) || target.name !== "amount_chf") {
            return;
        }
        var panel = target.closest("#tip-panel");
        if (panel) {
            setPressed(panel, null);
        }
    });
})();
