"use client";

import { useEffect, useRef, useState } from "react";
import { cn } from "../cn";

// Three 0-9 copies: the middle one is home position, the outer ones give each
// reel room to roll a full turn in either direction through the digits in
// between (e.g. 9 -> 1 can go up through 0 instead of falling back 8 steps).
const REEL = Array.from({ length: 30 }, (_, i) => i % 10);
const DURATION_MS = 700;

export type RollingDirection = "up" | "down";

function RollingDigit({
  digit,
  direction,
  delayMs,
}: {
  digit: number;
  direction: RollingDirection;
  delayMs: number;
}) {
  const stripRef = useRef<HTMLSpanElement>(null);
  const [position, setPosition] = useState(10 + digit);
  const [animated, setAnimated] = useState(false);
  // Last committed strip position — the animation may still be in flight, but
  // distances are computed modulo 10 so the logical digit is always right.
  const positionRef = useRef(10 + digit);
  const entered = useRef(false);

  // Entry spin, starting from 0: the strip's resting transform is already the
  // home cell, so this plays one imperative roll from the "0" cell onto
  // `digit`. WAAPI rather than a CSS state change — mount-time class/transform
  // pairs can land in the same style update and silently skip the transition.
  useEffect(() => {
    const el = stripRef.current;
    if (!el || entered.current) return;
    entered.current = true;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    if (digit === 0) return;
    // The "0" cell of the middle copy for an upward roll, of the top copy for
    // a downward one — both land on the home cell after `dist` steps.
    const start = direction === "down" ? 20 : 10;
    el.animate(
      [
        { transform: `translateY(-${start}em)` },
        { transform: `translateY(-${10 + digit}em)` },
      ],
      // fill: backwards — the "0" frame must hold during the stagger delay,
      // otherwise the resting value flashes before the roll starts.
      {
        duration: DURATION_MS,
        delay: delayMs,
        easing: "ease-out",
        fill: "backwards",
      }
    );
  }, [digit, direction, delayMs]);

  useEffect(() => {
    const current = ((positionRef.current % 10) + 10) % 10;
    const home = 10 + digit;
    const dist =
      direction === "down"
        ? (current - digit + 10) % 10
        : (digit - current + 10) % 10;

    if (dist > 0) {
      // Drop any in-flight entry roll so it can't override this transition.
      stripRef.current?.getAnimations().forEach((a) => a.cancel());
      const target =
        direction === "down" ? 10 + current - dist : 10 + current + dist;
      positionRef.current = target;
      setAnimated(true);
      setPosition(target);
    }
    // Any pending target already displays `digit`, so once the reel lands we
    // snap back to its home copy (invisible) to keep the strip centered.
    // Re-arming on every run makes this StrictMode-safe: the double-invoked
    // mount effect just reschedules the snap instead of cancelling the entry
    // spin with an instant reset.
    if (positionRef.current !== home) {
      const timer = setTimeout(() => {
        positionRef.current = home;
        setAnimated(false);
        setPosition(home);
      }, DURATION_MS + delayMs + 60);
      return () => clearTimeout(timer);
    }
  }, [digit, direction, delayMs]);

  return (
    <span className="inline-block h-[1em] overflow-hidden">
      <span
        ref={stripRef}
        className={cn(
          "flex flex-col ease-out motion-reduce:transition-none",
          animated && "transition-transform duration-700"
        )}
        style={{
          transform: `translateY(-${position}em)`,
          transitionDelay: `${delayMs}ms`,
        }}
      >
        {REEL.map((d, i) => (
          <span key={i} className="block h-[1em] leading-none">
            {d}
          </span>
        ))}
      </span>
    </span>
  );
}

export function RollingNumber({
  value,
  locale = "fr-FR",
  direction = "random",
  className,
}: {
  value: number;
  locale?: string;
  direction?: RollingDirection | "random";
  className?: string;
}) {
  const formatted = value.toLocaleString(locale);
  const chars = formatted.split("");
  // One direction per animation, shared by every reel: picked when the value
  // changes ("random" = coin flip, so consecutive refreshes may differ). Also
  // drives the entry spin on mount.
  const [spinDirection, setSpinDirection] = useState<RollingDirection>(() =>
    direction === "random" ? (Math.random() < 0.5 ? "up" : "down") : direction
  );
  const [prevFormatted, setPrevFormatted] = useState(formatted);
  if (prevFormatted !== formatted) {
    setPrevFormatted(formatted);
    setSpinDirection(
      direction === "random"
        ? Math.random() < 0.5
          ? "up"
          : "down"
        : direction
    );
  }

  return (
    <span className={cn("inline-flex tabular-nums", className)}>
      <span className="sr-only">{formatted}</span>
      <span aria-hidden="true" className="contents">
        {chars.map((char, i) => {
          // Position from the right maps to digit significance, so keying on
          // it keeps every reel mounted when the width changes (999 -> 1 000).
          const pos = chars.length - 1 - i;
          return /\d/.test(char) ? (
            <RollingDigit
              key={`d${pos}`}
              digit={Number(char)}
              direction={spinDirection}
              delayMs={pos * 60}
            />
          ) : (
            // Non-digit chars (fr-FR narrow no-break space) stay static.
            <span key={`c${i}`}>{char}</span>
          );
        })}
      </span>
    </span>
  );
}
