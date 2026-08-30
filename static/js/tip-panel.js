// Tip panel preset amounts (VERB-110 panel, VERB-112 mount).
//
// Progressive enhancement only: clicking a preset writes its whole-CHF value
// into the amount_chf number input, which is a fully working control on its
// own. Nothing here is load-bearing.
//
// A file rather than an inline <script> because the panel's real mount is the
// confirmed-match page, inside the HTMX-swapped #match-actions block. htmx
// re-creates script nodes it swaps in, and a re-created inline script loses
// its CSP nonce, so it is blocked under the production policy (script-src
// 'self' 'nonce-...') — and silently allowed by the report-only policy in
// development, which hides the breakage. A same-origin src is covered by
// 'self' at both mounts.
//
// The handler is delegated from document and registered once, so re-executing
// this file after a later swap is a no-op.
(function () {
    if (window.__tipPanelPresetsBound) {
        return;
    }
    window.__tipPanelPresetsBound = true;

    document.addEventListener("click", function (event) {
        var target = event.target;
        if (!(target instanceof Element)) {
            return;
        }
        var button = target.closest(".tip-preset");
        if (!button) {
            return;
        }
        var panel = button.closest("#tip-panel");
        var amountInput =
            panel && panel.querySelector('input[name="amount_chf"]');
        if (amountInput) {
            amountInput.value = button.getAttribute("data-tip-amount");
        }
    });
})();
