import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { cn } from "@/lib/utils"
import { Slot } from "radix-ui"

function BubbleGroup({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="bubble-group"
      className={cn("cc-ui-bubble-group flex min-w-0 flex-col", className)}
      {...props}
    />
  )
}

const bubbleVariants = cva(
  "cc-ui-bubble group/bubble relative flex w-fit min-w-0 flex-col",
  {
    variants: {
      variant: {
        default: "cc-ui-bubble-variant-default",
        secondary: "cc-ui-bubble-variant-secondary",
        muted: "cc-ui-bubble-variant-muted",
        tinted: "cc-ui-bubble-variant-tinted",
        outline: "cc-ui-bubble-variant-outline",
        ghost: "cc-ui-bubble-variant-ghost",
        destructive: "cc-ui-bubble-variant-destructive",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Bubble({
  variant = "default",
  align = "start",
  className,
  ...props
}: React.ComponentProps<"div"> &
  VariantProps<typeof bubbleVariants> & {
    align?: "start" | "end"
  }) {
  return (
    <div
      data-slot="bubble"
      data-variant={variant}
      data-align={align}
      className={cn(bubbleVariants({ variant }), className)}
      {...props}
    />
  )
}

function BubbleContent({
  asChild = false,
  className,
  ...props
}: React.ComponentProps<"div"> & {
  asChild?: boolean
}) {
  const Comp = asChild ? Slot.Root : "div"

  return (
    <Comp
      data-slot="bubble-content"
      className={cn(
        "cc-ui-bubble-content w-fit max-w-full min-w-0 overflow-hidden wrap-break-word [button]:text-left [button,a]:transition-colors",
        className
      )}
      {...props}
    />
  )
}

const bubbleReactionsVariants = cva(
  "cc-ui-bubble-reactions absolute flex w-fit items-center justify-center",
  {
    variants: {
      side: {
        top: "cc-ui-bubble-reactions-side-top",
        bottom: "cc-ui-bubble-reactions-side-bottom",
      },
      align: {
        start: "cc-ui-bubble-reactions-align-start",
        end: "cc-ui-bubble-reactions-align-end",
      },
    },
    defaultVariants: {
      side: "bottom",
      align: "end",
    },
  }
)

function BubbleReactions({
  side = "bottom",
  align = "end",
  className,
  ...props
}: React.ComponentProps<"div"> & {
  align?: "start" | "end"
  side?: "top" | "bottom"
}) {
  return (
    <div
      data-slot="bubble-reactions"
      data-align={align}
      data-side={side}
      className={cn(bubbleReactionsVariants({ side, align }), className)}
      {...props}
    />
  )
}

export { BubbleGroup, Bubble, BubbleContent, BubbleReactions }
