/**
 * CustomSelect — fully styled listbox replacement for native <select>.
 *
 * The browser's native <select> popup can't be styled — its option list
 * is drawn by the OS. This component renders a React-controlled list so
 * hover, selected, and disabled states match the rest of the UI.
 *
 * Props:
 *   value         current value (compared with String() coercion)
 *   onChange      called with { target: { value } } — matches the <select> API
 *   options       [{ value, label }] — value can be any primitive
 *   placeholder   text shown when nothing is selected
 *   disabled      boolean
 *   size          "sm" | "md" (default "md")
 *   leadingIcon   ReactNode rendered on the left side of the trigger
 *   className     extra classes on the root wrapper
 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export default function CustomSelect({
  value,
  onChange,
  options,
  placeholder = "Select…",
  disabled = false,
  size = "md",
  leadingIcon = null,
  className = "",
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef(null);
  const triggerRef = useRef(null);
  const listRef = useRef(null);
  const [menuRect, setMenuRect] = useState(null); // { left, top, width }

  // Position the portaled menu under the trigger and keep it aligned
  // through scrolls / resizes. Flips up if there isn't enough room below.
  const recalc = () => {
    const btn = triggerRef.current;
    if (!btn) return;
    const r = btn.getBoundingClientRect();
    const spaceBelow = window.innerHeight - r.bottom;
    const menuH = listRef.current?.offsetHeight ?? 240;
    const flipUp = spaceBelow < menuH + 16 && r.top > menuH + 16;
    setMenuRect({
      left: r.left,
      top: flipUp ? r.top - menuH - 6 : r.bottom + 6,
      width: r.width,
    });
  };

  useLayoutEffect(() => {
    if (!open) return;
    recalc();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e) => {
      const inTrigger = rootRef.current && rootRef.current.contains(e.target);
      const inList = listRef.current && listRef.current.contains(e.target);
      if (!inTrigger && !inList) setOpen(false);
    };
    const onEsc = (e) => e.key === "Escape" && setOpen(false);
    const onScrollOrResize = () => recalc();
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onEsc);
    window.addEventListener("scroll", onScrollOrResize, true);
    window.addEventListener("resize", onScrollOrResize);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onEsc);
      window.removeEventListener("scroll", onScrollOrResize, true);
      window.removeEventListener("resize", onScrollOrResize);
    };
  }, [open]);

  const selected = options.find((o) => String(o.value) === String(value));
  const labelText = selected?.label ?? placeholder;

  const sizeCls = size === "sm" ? "text-xs py-1.5" : "text-sm py-2";
  const padLeft = leadingIcon ? "pl-7" : "pl-3";

  return (
    <div ref={rootRef} className={`relative ${className}`}>
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`w-full appearance-none bg-background hover:bg-primary/5 border ${
          open
            ? "border-primary ring-2 ring-primary/15"
            : "border-primary/15 hover:border-primary/30"
        } rounded-xl ${padLeft} pr-9 ${sizeCls} font-semibold text-text-main text-left outline-none cursor-pointer transition-all disabled:opacity-60 disabled:cursor-not-allowed flex items-center`}
      >
        {leadingIcon && (
          <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-primary pointer-events-none">
            {leadingIcon}
          </span>
        )}
        <span className={`truncate ${selected ? "" : "text-text-light"}`}>
          {labelText}
        </span>
        <span
          className={`absolute right-3 top-1/2 -translate-y-1/2 text-text-muted transition-transform ${
            open ? "rotate-180" : ""
          }`}
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            className="h-3.5 w-3.5"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2.5}
              d="M19 9l-7 7-7-7"
            />
          </svg>
        </span>
      </button>

      {open && menuRect && createPortal(
        <ul
          ref={listRef}
          role="listbox"
          style={{
            position: "fixed",
            left: menuRect.left,
            top: menuRect.top,
            width: menuRect.width,
            zIndex: 1300,
          }}
          className="max-h-60 overflow-auto rounded-xl bg-card border border-primary/15 shadow-lg shadow-primary/10 py-1 animate-fade-in"
        >
          {options.length === 0 ? (
            <li className="px-3 py-2 text-xs text-text-light">No options</li>
          ) : (
            options.map((o) => {
              const isSel = String(o.value) === String(value);
              return (
                <li
                  key={o.value ?? "_empty"}
                  role="option"
                  aria-selected={isSel}
                  onClick={() => {
                    onChange({ target: { value: o.value } });
                    setOpen(false);
                  }}
                  className={`px-3 py-2 text-sm font-semibold cursor-pointer flex items-center justify-between gap-2 ${
                    isSel
                      ? "bg-primary/10 text-primary"
                      : "text-text-main hover:bg-primary/5"
                  }`}
                >
                  <span className="truncate">{o.label}</span>
                  {isSel && (
                    <svg
                      xmlns="http://www.w3.org/2000/svg"
                      className="h-3.5 w-3.5 shrink-0"
                      fill="none"
                      viewBox="0 0 24 24"
                      stroke="currentColor"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        strokeWidth={3}
                        d="M5 13l4 4L19 7"
                      />
                    </svg>
                  )}
                </li>
              );
            })
          )}
        </ul>,
        document.body,
      )}
    </div>
  );
}
