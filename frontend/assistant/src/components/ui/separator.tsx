
import type * as React from "react";
import { Separator as SeparatorPrimitive } from "radix-ui";

import { cn } from "@/lib/utils";

function Separator({
  className,
  orientation = "horizontal",
  decorative = true,
  ...props
}: React.ComponentProps<typeof SeparatorPrimitive.Root>) {
  return (
    <SeparatorPrimitive.Root
      data-slot="separator"
      decorative={decorative}
      orientation={orientation}
      className={cn(
        "shrink-0 data-[orientation=horizontal]:w-full data-[orientation=horizontal]:border-t data-[orientation=vertical]:h-full data-[orientation=vertical]:border-s",
        className,
      )}
      {...props}
    />
  );
}

export { Separator };
