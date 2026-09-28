"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";

type Props = {
  label: React.ReactNode;
  title: string;
  explanation: string;
  formula?: string;
  href?: string;
};

/** A term with an explanation: the term itself is a button (a disclosure: keyboard and touch
 *  work, nothing depends on hover). Escape or a click outside closes it. The explanation is in
 *  the server-rendered HTML (hidden until opened). */
export default function TermPopover({ label, title, explanation, formula, href }: Props) {
  const [open, setOpen] = useState(false);
  const [shift, setShift] = useState(0);
  const id = useId();
  const wrap = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLSpanElement>(null);
  const panel = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        button.current?.focus();
      }
    }
    function onPointer(e: PointerEvent) {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    document.addEventListener("pointerdown", onPointer);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  function toggle() {
    setShift(0);
    setOpen((o) => !o);
  }

  // keep the panel inside the viewport (narrow phones)
  useLayoutEffect(() => {
    if (!open || !panel.current) return;
    const r = panel.current.getBoundingClientRect();
    const margin = 8;
    const over = r.right + shift - (window.innerWidth - margin);
    const under = margin - (r.left + shift);
    if (over > 0) setShift((s) => s - over);
    else if (under > 0) setShift((s) => s + under);
    // measured once per opening
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  return (
    <span ref={wrap} className="relative inline">
      {/* A span with the button role rather than a <button>: a button is an inline block, so
          the line could break between the term and the punctuation after it. */}
      <span
        ref={button}
        role="button"
        tabIndex={0}
        aria-expanded={open}
        aria-controls={id}
        onClick={toggle}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            toggle();
          }
        }}
        className="term-button"
      >
        {label}
        <span className="sr-only"> (explain)</span>
      </span>
      <span
        ref={panel}
        id={id}
        role="note"
        hidden={!open}
        style={{ transform: `translateX(${shift}px)` }}
        className="absolute left-0 top-full z-50 mt-2 block w-[min(22rem,calc(100vw-2rem))] rounded-md border border-line bg-surface p-3 text-left text-sm font-normal normal-case leading-snug tracking-normal text-fg shadow-lg"
      >
        <span className="block font-semibold">{title}</span>
        <span className="mt-1 block">{explanation}</span>
        {formula ? (
          <span className="mt-2 block text-muted">
            <span className="font-medium">How it is computed: </span>
            {formula}
          </span>
        ) : null}
        {href ? (
          <a href={href} className="mt-2 block">
            More in the glossary
          </a>
        ) : null}
      </span>
    </span>
  );
}
