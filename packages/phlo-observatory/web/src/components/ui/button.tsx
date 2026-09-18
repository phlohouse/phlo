/**
 * Button primitive (shadcn registry, Base UI).
 */
import { Button as ButtonPrimitive } from "@base-ui/react/button";
import {  cva } from "class-variance-authority";
import type {VariantProps} from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * Paper control scale: 34px tall, 5px radius, 12px semibold label.
 * Sizes stay on shadcn's names so upstream examples port directly.
 */
const buttonVariants = cva(
  "group/button inline-flex shrink-0 cursor-pointer items-center justify-center gap-1.5 rounded-[5px] border border-transparent text-xs font-semibold whitespace-nowrap transition-colors outline-none select-none focus-visible:ring-2 focus-visible:ring-ring/40 disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-[15px]",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground hover:bg-primary/90",
        outline: "border-border bg-card text-foreground hover:bg-secondary",
        secondary: "bg-secondary text-secondary-foreground hover:bg-accent",
        ghost: "text-foreground hover:bg-muted",
        destructive: "bg-destructive text-destructive-foreground hover:bg-destructive/90",
        link: "text-primary-strong underline-offset-4 hover:underline",
      },
      size: {
        default: "h-[34px] px-[11px]",
        xs: "h-6 gap-1 px-2 text-[11px]",
        sm: "h-7 px-2.5 text-[11px]",
        lg: "h-10 px-4 text-sm",
        icon: "size-[34px]",
        "icon-xs": "size-6",
        "icon-sm": "size-7",
        "icon-lg": "size-10",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

function Button({
  className,
  variant = "default",
  size = "default",
  ...props
}: ButtonPrimitive.Props & VariantProps<typeof buttonVariants>) {
  return (
    <ButtonPrimitive
      data-slot="button"
      className={cn(buttonVariants({ variant, size, className }))}
      {...props}
    />
  );
}

export { Button, buttonVariants };
