/**
 * Tailwind class merge helper shared by every component.
 */
import {  clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import type {ClassValue} from "clsx";

export function cn(...inputs: Array<ClassValue>) {
  return twMerge(clsx(inputs));
}

/**
 * Compact timestamp for dense tables: `Sep 19 · 09:52`. Non-ISO values pass
 * through unchanged so a provider's own rendering is never mangled.
 */
export function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const day = date.toLocaleDateString([], { month: "short", day: "numeric" });
  const time = date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${day} · ${time}`;
}
