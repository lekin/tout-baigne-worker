"use client";

import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

// Table cells live inside `overflow` containers (the table scroller and the
// card), so an absolutely-positioned popup gets clipped. This renders the
// popup through a portal with `position: fixed` — it always escapes.
export function CellTooltip({
  children,
  content,
  className,
  contentWidth = 288,
}: {
  children: React.ReactNode;
  content: React.ReactNode;
  className?: string;
  contentWidth?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const hovered = useRef(false);
  const [isOpen, setIsOpen] = useState(false);
  const id = useId();

  const cancelClose = useCallback(() => {
    if (closeTimer.current !== null) {
      clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
  }, []);

  const open = () => {
    cancelClose();
    setIsOpen(true);
  };

  const scheduleClose = () => {
    cancelClose();
    closeTimer.current = setTimeout(() => {
      closeTimer.current = null;
      const activeElement = document.activeElement;
      if (
        !hovered.current &&
        !ref.current?.contains(activeElement) &&
        !popupRef.current?.contains(activeElement)
      ) {
        setIsOpen(false);
      }
    }, 150);
  };

  const onMouseEnter = () => {
    hovered.current = true;
    open();
  };

  const onMouseLeave = () => {
    hovered.current = false;
    scheduleClose();
  };

  useEffect(() => cancelClose, [cancelClose]);

  useLayoutEffect(() => {
    if (!isOpen) return;
    const trigger = ref.current;
    const popup = popupRef.current;
    if (!trigger || !popup) return;

    const viewport = window.visualViewport;
    const reposition = () => {
      const margin = 8;
      const gap = 6;
      const viewportLeft = viewport?.offsetLeft ?? 0;
      const viewportTop = viewport?.offsetTop ?? 0;
      const viewportWidth = viewport?.width ?? document.documentElement.clientWidth;
      const viewportHeight = viewport?.height ?? window.innerHeight;
      const minLeft = viewportLeft + margin;
      const minTop = viewportTop + margin;
      const maxRight = viewportLeft + viewportWidth - margin;
      const maxBottom = viewportTop + viewportHeight - margin;

      popup.style.maxWidth = `${Math.max(0, viewportWidth - margin * 2)}px`;
      popup.style.maxHeight = `${Math.max(0, viewportHeight - margin * 2)}px`;

      const target = trigger.getBoundingClientRect();
      const { width, height } = popup.getBoundingClientRect();
      const below = maxBottom - target.bottom - gap;
      const above = target.top - gap - minTop;
      const top = height > below && above > below
        ? target.top - gap - height
        : target.bottom + gap;

      popup.style.left = `${Math.max(minLeft, Math.min(target.left, maxRight - width))}px`;
      popup.style.top = `${Math.max(minTop, Math.min(top, maxBottom - height))}px`;
      popup.style.visibility = "visible";
    };

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        hovered.current = false;
        cancelClose();
        if (popup.contains(document.activeElement)) {
          trigger.focus({ preventScroll: true });
        }
        setIsOpen(false);
      }
    };

    reposition();
    const observer = new ResizeObserver(reposition);
    observer.observe(trigger);
    observer.observe(popup);
    window.addEventListener("resize", reposition);
    window.addEventListener("scroll", reposition, true);
    viewport?.addEventListener("resize", reposition);
    viewport?.addEventListener("scroll", reposition);
    document.addEventListener("keydown", onKeyDown);

    return () => {
      observer.disconnect();
      window.removeEventListener("resize", reposition);
      window.removeEventListener("scroll", reposition, true);
      viewport?.removeEventListener("resize", reposition);
      viewport?.removeEventListener("scroll", reposition);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [isOpen, contentWidth, cancelClose]);

  return (
    <>
      <div
        ref={ref}
        className={className}
        tabIndex={0}
        aria-describedby={isOpen ? id : undefined}
        onMouseEnter={onMouseEnter}
        onMouseLeave={onMouseLeave}
        onFocus={open}
        onBlur={scheduleClose}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && event.target === event.currentTarget && isOpen) {
            event.preventDefault();
            popupRef.current?.focus({ preventScroll: true });
          }
        }}
      >
        {children}
      </div>
      {isOpen &&
        createPortal(
          <div
            ref={popupRef}
            id={id}
            role="tooltip"
            tabIndex={0}
            className="fixed z-50 overflow-auto overscroll-contain rounded-lg bg-popover px-3 py-2 text-xs text-popover-foreground shadow-md ring-1 ring-foreground/10"
            style={{
              left: 0,
              top: 0,
              width: contentWidth,
              maxWidth: "calc(100vw - 16px)",
              maxHeight: "calc(100dvh - 16px)",
              visibility: "hidden",
            }}
            onMouseEnter={onMouseEnter}
            onMouseLeave={onMouseLeave}
            onFocus={open}
            onBlur={scheduleClose}
          >
            {content}
          </div>,
          document.body
        )}
    </>
  );
}
