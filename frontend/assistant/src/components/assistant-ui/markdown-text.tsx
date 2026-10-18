
// 正文的块照 Claude Code（设计文档 3.10）：块之间只用上外边距——段落、列表、表格、代码、引用 --cc-md-block（0.75em），
// 标题前 --cc-md-heading（1.25em），第一块 0；表格细线、外圈 1px 环加圆角。
// 不用组件库的圆点光标（styles/dot.css）：同 Claude，回复在出时由对话底部的工作状态行表示（thread.tsx 的 RunIndicator）。
// 那个光标挂在「这一段还在出」上，正文刚开头还没字时只剩一个黑点、正文出完还要等结束才消失（教程第 8 条 b、c）。
import {
  type CodeHeaderProps,
  MarkdownTextPrimitive,
  unstable_memoizeMarkdownComponents as memoizeMarkdownComponents,
  useIsMarkdownCodeBlock,
} from "@assistant-ui/react-markdown";
import remarkGfm from "remark-gfm";
import { type FC, memo, useState } from "react";
import { CheckIcon, CopyIcon } from "lucide-react";
import { t } from "@/i18n";

import { TooltipIconButton } from "@/components/assistant-ui/tooltip-icon-button";
import { cn } from "@/lib/utils";

const MarkdownTextImpl = () => {
  return (
    <MarkdownTextPrimitive
      remarkPlugins={[remarkGfm]}
      className="aui-md"
      components={defaultComponents}
      // 不延后解析：逐字出字时延后的那份一直被新字打断，慢的手机上几秒都只看到空白（教程第 8 条 b）
    />
  );
};

export const MarkdownText = memo(MarkdownTextImpl);

const CodeHeader: FC<CodeHeaderProps> = ({ language, code }) => {
  const { isCopied, copyToClipboard } = useCopyToClipboard();
  const onCopy = () => {
    if (!code || isCopied) return;
    copyToClipboard(code);
  };

  return (
    <div className="aui-code-header-root border-border/50 bg-muted/50 mt-(--cc-md-block) first:mt-0 flex items-center justify-between rounded-t-xl border border-b-0 px-3.5 py-1.5 text-caption">
      <span className="aui-code-header-language text-muted-foreground font-medium lowercase">
        {language}
      </span>
      <TooltipIconButton tooltip={t("copy")} onClick={onCopy}>
        {!isCopied && (
          <CopyIcon className="animate-in zoom-in-75 fade-in duration-150" />
        )}
        {isCopied && (
          <CheckIcon className="animate-in zoom-in-50 fade-in duration-200 ease-out" />
        )}
      </TooltipIconButton>
    </div>
  );
};

const useCopyToClipboard = ({
  copiedDuration = 3000,
}: {
  copiedDuration?: number;
} = {}) => {
  const [isCopied, setIsCopied] = useState<boolean>(false);

  const copyToClipboard = (value: string) => {
    if (!value || typeof navigator === "undefined" || !navigator.clipboard) {
      return;
    }

    navigator.clipboard.writeText(value).then(
      () => {
        setIsCopied(true);
        setTimeout(() => setIsCopied(false), copiedDuration);
      },
      () => {},
    );
  };

  return { isCopied, copyToClipboard };
};

const defaultComponents = memoizeMarkdownComponents({
  h1: ({ className, ...props }) => (
    <h1
      className={cn(
        "aui-md-h1 mt-(--cc-md-heading) scroll-m-(--cc-md-heading-scroll) text-(length:--cc-md-h1) leading-(--cc-md-heading-lh) font-semibold first:mt-0",
        className,
      )}
      {...props}
    />
  ),
  h2: ({ className, ...props }) => (
    <h2
      className={cn(
        "aui-md-h2 mt-(--cc-md-heading) scroll-m-(--cc-md-heading-scroll) text-(length:--cc-md-h2) leading-(--cc-md-heading-lh) font-semibold first:mt-0",
        className,
      )}
      {...props}
    />
  ),
  h3: ({ className, ...props }) => (
    <h3
      className={cn(
        "aui-md-h3 mt-(--cc-md-heading) scroll-m-(--cc-md-heading-scroll) font-semibold first:mt-0",
        className,
      )}
      {...props}
    />
  ),
  h4: ({ className, ...props }) => (
    <h4
      className={cn(
        "aui-md-h4 mt-(--cc-md-heading) scroll-m-(--cc-md-heading-scroll) font-medium first:mt-0",
        className,
      )}
      {...props}
    />
  ),
  h5: ({ className, ...props }) => (
    <h5
      className={cn(
        "aui-md-h5 mt-(--cc-md-heading) font-semibold first:mt-0",
        className,
      )}
      {...props}
    />
  ),
  h6: ({ className, ...props }) => (
    <h6
      className={cn(
        "aui-md-h6 mt-(--cc-md-heading) font-medium first:mt-0",
        className,
      )}
      {...props}
    />
  ),
  p: ({ className, ...props }) => (
    <p
      className={cn(
        "aui-md-p mt-(--cc-md-block) first:mt-0",
        className,
      )}
      {...props}
    />
  ),
  a: ({ className, ...props }) => (
    <a
      className={cn(
        "aui-md-a text-link hover:text-link/80 underline underline-offset-(--cc-link-offset)",
        className,
      )}
      {...props}
    />
  ),
  blockquote: ({ className, ...props }) => (
    <blockquote
      className={cn(
        "aui-md-blockquote border-muted-foreground/30 text-muted-foreground mt-(--cc-md-block) border-s-(length:--cc-md-quote-w) ps-(--cc-space-16) first:mt-0",
        className,
      )}
      {...props}
    />
  ),
  ul: ({ className, ...props }) => (
    <ul
      className={cn(
        "aui-md-ul marker:text-muted-foreground mt-(--cc-md-block) ps-(--cc-md-list-indent) list-disc first:mt-0 [&>li]:mt-(--cc-md-li-gap) [&>li]:ps-(--cc-md-li-indent)",
        className,
      )}
      {...props}
    />
  ),
  ol: ({ className, ...props }) => (
    <ol
      className={cn(
        "aui-md-ol marker:text-muted-foreground mt-(--cc-md-block) ps-(--cc-md-list-indent) list-decimal first:mt-0 [&>li]:mt-(--cc-md-li-gap) [&>li]:ps-(--cc-md-li-indent)",
        className,
      )}
      {...props}
    />
  ),
  hr: ({ className, ...props }) => (
    <hr
      className={cn("aui-md-hr border-line mt-(--cc-md-block) first:mt-0", className)}
      {...props}
    />
  ),
  // 宽表格在对话里横向滚动，单元格不被挤到一字一行（右侧结果面板开着时对话区变窄，2026-10-10 测试泰文名竖排）。
  // 外圈 1px 环加圆角，里面细线：表头下、行间、列间
  table: ({ className, ...props }) => (
    <div className="aui-md-table-wrap mt-(--cc-md-block) w-full overflow-x-auto rounded-lg border-(length:--cc-ring-w) border-line first:mt-0">
      <table
        className={cn("aui-md-table cc-table w-full border-separate border-spacing-0", className)}
        {...props}
      />
    </div>
  ),
  th: ({ className, ...props }) => (
    <th
      className={cn(
        "aui-md-th border-line border-b not-first:border-s [[align=center]]:text-center [[align=right]]:text-right",
        className,
      )}
      {...props}
    />
  ),
  td: ({ className, ...props }) => (
    <td
      className={cn(
        "aui-md-td border-line border-b not-first:border-s [[align=center]]:text-center [[align=right]]:text-right",
        className,
      )}
      {...props}
    />
  ),
  tr: ({ className, ...props }) => (
    <tr
      className={cn(
        "aui-md-tr m-0 p-0 [tbody>&:last-child>td]:border-b-0",
        className,
      )}
      {...props}
    />
  ),
  li: ({ className, ...props }) => (
    <li className={cn("aui-md-li", className)} {...props} />
  ),
  strong: ({ className, ...props }) => (
    <strong
      className={cn("aui-md-strong font-semibold", className)}
      {...props}
    />
  ),
  sup: ({ className, ...props }) => (
    <sup
      className={cn("aui-md-sup [&>a]:text-caption [&>a]:no-underline", className)}
      {...props}
    />
  ),
  pre: ({ className, ...props }) => (
    <pre
      className={cn(
        "aui-md-pre border-border/50 bg-muted/30 overflow-x-auto rounded-t-none rounded-b-xl border border-t-0 p-3.5 text-(length:--cc-text-sm) leading-relaxed",
        className,
      )}
      {...props}
    />
  ),
  code: function Code({ className, ...props }) {
    const isCodeBlock = useIsMarkdownCodeBlock();
    return (
      <code
        className={cn(
          !isCodeBlock &&
            "aui-md-inline-code bg-code-bg text-code-ink rounded-code px-(--cc-space-4) py-(--cc-space-2) font-mono text-(length:--cc-code-inline-size)",
          className,
        )}
        {...props}
      />
    );
  },
  CodeHeader,
});
