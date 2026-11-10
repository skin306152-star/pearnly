import * as React from "react"
import { cn } from "@/lib/utils"
import { Switch as SwitchPrimitive } from "radix-ui"

function Switch({
  className,
  size = "default",
  ...props
}: React.ComponentProps<typeof SwitchPrimitive.Root> & {
  size?: "sm" | "default"
}) {
  return (
    <SwitchPrimitive.Root
      data-slot="switch"
      data-size={size}
      className={cn(
        "cc-switch cc-ui-switch peer group/switch relative inline-flex items-center outline-none data-disabled:cursor-not-allowed",
        className
      )}
      {...props}
    />
  )
}

export { Switch }
