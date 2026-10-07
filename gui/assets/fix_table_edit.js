// Fix Dash DataTable inline-editing UX (segment-table Name column).
//
// Problem: DataTable has a hidden "active" state (single-click) that is NOT
// the same as "editing" (double-click / Enter).  In the active state:
//   - Typing *replaces* the entire cell value (instead of appending at a cursor)
//   - Backspace / Delete silently wipe the value with no undo
// This makes editing feel broken and causes silent, unrecoverable data loss.
//
// Fix (two layers):
//   1. Block Backspace / Delete when focus is on a bare <td> (active state)
//      rather than an <input> (editing state) — prevents data loss.
//   2. Auto-enter true editing mode on single-click so the user gets a real
//      text cursor immediately — prevents the confusing intermediate state.

(function () {
    "use strict";

    // --- Layer 1: block destructive keys in active-but-not-editing state ------
    document.addEventListener("keydown", function (e) {
        if (e.key !== "Backspace" && e.key !== "Delete") return;
        var el = document.activeElement;
        // A bare <td> with focus means "active" (not editing).  An <input> or
        // <textarea> inside the cell means the user is truly editing — let it
        // through.
        if (el && el.tagName === "TD" &&
            el.closest(".dash-spreadsheet-container")) {
            e.preventDefault();
            e.stopPropagation();
        }
    }, true);  // capture phase — fires before DataTable's own handler

    // --- Layer 2: auto-enter edit mode on single-click -----------------------
    // When a click lands inside the spreadsheet, wait a tick for DataTable to
    // set its "active" state, then synthesise an Enter keypress to transition
    // into true editing mode (cursor inside an <input>).
    document.addEventListener("click", function (e) {
        // Don't interfere with row-selection checkboxes or header sorting.
        if (e.target.closest("th") ||
            e.target.closest("input[type='checkbox']") ||
            e.target.closest("label")) {
            return;
        }
        var td = e.target.closest("td");
        if (!td || !td.closest(".dash-spreadsheet-container")) return;

        setTimeout(function () {
            var focused = document.activeElement;
            if (focused && focused.tagName === "TD" &&
                focused.closest(".dash-spreadsheet-container")) {
                // Still on a bare <td> — nudge into editing mode.
                focused.dispatchEvent(new KeyboardEvent("keydown", {
                    key: "Enter", code: "Enter",
                    keyCode: 13, which: 13,
                    bubbles: true, cancelable: true
                }));
            }
        }, 30);
    }, true);

    // --- Layer 1b: also block bare character keys that silently replace -------
    // In the "active" state, typing any printable character replaces the entire
    // cell value.  Since Layer 2 should prevent this state from persisting, this
    // is a safety net for the brief window before the auto-Enter fires.
    document.addEventListener("keydown", function (e) {
        // Only care about single printable characters (not modifiers, arrows, etc.)
        if (e.ctrlKey || e.altKey || e.metaKey) return;
        if (e.key.length !== 1) return;  // non-printable keys have longer names
        var el = document.activeElement;
        if (el && el.tagName === "TD" &&
            el.closest(".dash-spreadsheet-container")) {
            e.preventDefault();
            e.stopPropagation();
            // Enter edit mode, then re-dispatch the character so it appears
            // in the input field naturally.
            el.dispatchEvent(new KeyboardEvent("keydown", {
                key: "Enter", code: "Enter",
                keyCode: 13, which: 13,
                bubbles: true, cancelable: true
            }));
            // After edit mode activates, re-send the character to the input.
            setTimeout(function () {
                var input = document.activeElement;
                if (input && (input.tagName === "INPUT" || input.tagName === "TEXTAREA")) {
                    // Place cursor at end and insert the character
                    var len = input.value.length;
                    input.setSelectionRange(len, len);
                    input.dispatchEvent(new InputEvent("input", {
                        data: e.key, inputType: "insertText",
                        bubbles: true, cancelable: true
                    }));
                    // Some React versions need the native setter for controlled inputs
                    var nativeSetter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, "value"
                    ).set;
                    nativeSetter.call(input, input.value + e.key);
                    input.dispatchEvent(new Event("input", { bubbles: true }));
                }
            }, 50);
        }
    }, true);
})();
