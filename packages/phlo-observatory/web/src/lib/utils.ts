/**
 * Tailwind class merge helper shared by every component.
 */
import {  clsx } from "clsx";
import { twMerge } from "tailwind-merge";
import type {ClassValue} from "clsx";

export function cn(...inputs: Array<ClassValue>) {
  return twMerge(clsx(inputs));
}
