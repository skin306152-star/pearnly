import type * as React from "react"
import { Loader2Icon } from "lucide-react"

import { cn } from "@/lib/utils"
import { ui } from "@/i18n"

function Spinner({ className, ...props }: React.ComponentProps<"svg">) {
  return (
    <Loader2Icon
      data-slot="spinner"
      role="status"
      aria-label={ui.loading}
      className={cn("cc-spinner", className)}
      {...props}
    />
  )
}

export { Spinner }
