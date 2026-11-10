import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { Slot } from "radix-ui"

const badgeVariants = cva(
  "cc-chip cc-ui-badge group/badge inline-flex w-fit shrink-0 items-center justify-center overflow-hidden whitespace-nowrap aria-invalid:border-destructive [&>svg]:pointer-events-none",
  {
    variants: {
      variant: {
        default: "cc-ui-badge-variant-default",
        secondary: "cc-ui-badge-variant-secondary",
        destructive: "cc-ui-badge-variant-destructive",
        outline: "cc-ui-badge-variant-outline",
        ghost: "cc-ui-badge-variant-ghost",
        link: "cc-ui-badge-variant-link",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
