import * as React from "react"
import { CheckIcon } from "lucide-react"
import { Questionnaire as QuestionnairePrimitive } from "@shadcn/react/questionnaire"
import { cn } from "@/lib/utils"

import { buttonVariants, toneProps, type Button } from "@/components/ui/button"
import { ask, ui } from "@/i18n"

function Questionnaire({
  className,
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Root>) {
  return (
    <QuestionnairePrimitive.Root
      data-slot="questionnaire"
      className={cn("cc-ui-questionnaire flex w-full min-w-0 flex-col", className)}
      {...props}
    />
  )
}

// 进度字与无障碍名用我们的词条（包里默认是英文「Question n of m」）；调用方给了 children / render 就用调用方的
function QuestionnaireProgress({
  className,
  children,
  render,
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Progress>) {
  return (
    <QuestionnairePrimitive.Progress
      data-slot="questionnaire-progress"
      className={cn(
        "cc-ui-questionnaire-progress w-fit font-medium text-muted-foreground tabular-nums",
        className
      )}
      render={
        render ??
        (children === undefined
          ? (own, state) => {
              const text = state.total ? ask.nth(state.current, state.total) : undefined
              return <div {...own} aria-label={text} aria-valuetext={text}>{text}</div>
            }
          : undefined)
      }
      {...props}
    >
      {children}
    </QuestionnairePrimitive.Progress>
  )
}

function QuestionnaireItem({
  className,
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Item>) {
  return (
    <QuestionnairePrimitive.Item
      data-slot="questionnaire-item"
      className={cn(
        "cc-ui-questionnaire-item min-w-0 border-0 p-0 outline-none",
        className
      )}
      {...props}
    />
  )
}

function QuestionnaireTitle({
  className,
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Title>) {
  return (
    <QuestionnairePrimitive.Title
      data-slot="questionnaire-title"
      className={cn(
        "cc-ui-questionnaire-title text-pretty",
        className
      )}
      {...props}
    />
  )
}

function QuestionnaireDescription({
  className,
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Description>) {
  return (
    <QuestionnairePrimitive.Description
      data-slot="questionnaire-description"
      className={cn(
        "cc-ui-questionnaire-description text-pretty text-muted-foreground",
        className
      )}
      {...props}
    />
  )
}

function QuestionnaireChoices({
  className,
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Choices>) {
  return (
    <QuestionnairePrimitive.Choices
      data-slot="questionnaire-choices"
      className={cn(
        "cc-ui-questionnaire-choices group/questionnaire-choices grid min-w-0",
        className
      )}
      {...props}
    />
  )
}

function QuestionnaireChoice({
  children,
  className,
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Choice>) {
  return (
    <QuestionnairePrimitive.Choice
      data-slot="questionnaire-choice"
      className={cn(
        "cc-ui-questionnaire-choice group/questionnaire-choice relative flex cursor-pointer items-start text-start transition-colors outline-none select-none",
        "data-disabled:pointer-events-none data-disabled:cursor-not-allowed",
        className
      )}
      {...props}
    >
      <QuestionnairePrimitive.ChoiceInput
        data-slot="questionnaire-choice-input"
        className="cc-ui-questionnaire-choice-input absolute inset-0 size-full cursor-pointer opacity-0"
      />
      <span
        aria-hidden="true"
        data-slot="questionnaire-choice-indicator"
        className="cc-ui-questionnaire-choice-indicator pointer-events-none relative flex shrink-0 items-center justify-center border group-data-[type=radio]/questionnaire-choice:rounded-full"
      >
        <span
          data-slot="questionnaire-choice-indicator-dot"
          className="cc-ui-questionnaire-choice-indicator-dot hidden rounded-full group-data-[type=checkbox]/questionnaire-choice:hidden group-data-checked/questionnaire-choice:block"
        />
        <CheckIcon
          data-slot="questionnaire-choice-indicator-check"
          className="cc-ui-questionnaire-choice-indicator-check hidden group-data-[type=radio]/questionnaire-choice:hidden group-data-checked/questionnaire-choice:block"
        />
      </span>
      <QuestionnairePrimitive.ChoiceLabel
        data-slot="questionnaire-choice-label"
        className="cc-ui-questionnaire-choice-label cc-ui-questionnaire-choice-content flex min-w-0 flex-1 flex-col"
      >
        {children}
      </QuestionnairePrimitive.ChoiceLabel>
      <QuestionnairePrimitive.ChoiceShortcut
        data-slot="questionnaire-choice-shortcut"
        className="cc-ui-questionnaire-choice-shortcut cc-ui-questionnaire-shortcut pointer-events-none ms-auto hidden shrink-0 group-data-[shortcut]/questionnaire-choice:inline-flex"
      />
    </QuestionnairePrimitive.Choice>
  )
}

function QuestionnaireChoiceDescription({
  className,
  ...props
}: React.ComponentProps<"span">) {
  return (
    <span
      data-slot="questionnaire-choice-description"
      className={cn("cc-ui-questionnaire-choice-description", className)}
      {...props}
    />
  )
}

function QuestionnaireInput({
  className,
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Input>) {
  return (
    <div
      data-slot="questionnaire-input-wrapper"
      className="cc-ui-questionnaire-input-wrapper group/questionnaire-input relative min-w-0"
    >
      <QuestionnairePrimitive.Input
        data-slot="questionnaire-input"
        className={cn(
          "cc-input cc-ui-questionnaire-input w-full min-w-0 transition-[color,box-shadow,background-color] outline-none disabled:pointer-events-none disabled:cursor-not-allowed md:min-h-0",
          "placeholder:text-muted-foreground",
          className
        )}
        {...props}
      />
    </div>
  )
}

function QuestionnaireError({
  className,
  children,
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Error>) {
  return (
    <QuestionnairePrimitive.Error
      data-slot="questionnaire-error"
      className={cn("cc-ui-questionnaire-error text-destructive", className)}
      {...props}
    >
      {children ?? ui.chooseAnswer}
    </QuestionnairePrimitive.Error>
  )
}

function QuestionnaireActions({
  className,
  ...props
}: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="questionnaire-actions"
      className={cn(
        "cc-ui-questionnaire-actions grid w-full grid-cols-[minmax(0,1fr)_auto_auto] items-center",
        className
      )}
      {...props}
    />
  )
}

function QuestionnairePrevious({
  children,
  className,
  size = "xs",
  variant = "secondary",
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Previous> &
  Pick<React.ComponentProps<typeof Button>, "size" | "variant">) {
  return (
    <QuestionnairePrimitive.Previous
      data-slot="questionnaire-previous"
      data-size={size}
      data-variant={variant}
      {...toneProps(variant, size)}
      className={cn(
        buttonVariants({ size, variant }),
        "cc-ui-questionnaire-previous col-start-1 row-start-1 justify-self-start md:min-h-0",
        className
      )}
      {...props}
    >
      {children ?? ask.prev}
    </QuestionnairePrimitive.Previous>
  )
}

function QuestionnaireSkip({
  children,
  className,
  size = "xs",
  variant = "secondary",
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Skip> &
  Pick<React.ComponentProps<typeof Button>, "size" | "variant">) {
  return (
    <QuestionnairePrimitive.Skip
      data-slot="questionnaire-skip"
      data-size={size}
      data-variant={variant}
      {...toneProps(variant, size)}
      className={cn(
        buttonVariants({ size, variant }),
        "cc-ui-questionnaire-skip col-start-2 row-start-1 justify-self-end md:min-h-0",
        className
      )}
      {...props}
    >
      {children ?? ask.skip}
    </QuestionnairePrimitive.Skip>
  )
}

function QuestionnaireNext({
  children,
  className,
  size = "xs",
  variant = "default",
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Next> &
  Pick<React.ComponentProps<typeof Button>, "size" | "variant">) {
  return (
    <QuestionnairePrimitive.Next
      data-slot="questionnaire-next"
      data-size={size}
      data-variant={variant}
      {...toneProps(variant, size)}
      className={cn(
        buttonVariants({ size, variant }),
        "cc-ui-questionnaire-next col-start-3 row-start-1 justify-self-end md:min-h-0",
        className
      )}
      {...props}
    >
      {children ?? ask.next}
    </QuestionnairePrimitive.Next>
  )
}

function QuestionnaireSubmit({
  children,
  className,
  size = "xs",
  variant = "default",
  ...props
}: React.ComponentProps<typeof QuestionnairePrimitive.Submit> &
  Pick<React.ComponentProps<typeof Button>, "size" | "variant">) {
  return (
    <QuestionnairePrimitive.Submit
      data-slot="questionnaire-submit"
      data-size={size}
      data-variant={variant}
      {...toneProps(variant, size)}
      className={cn(
        buttonVariants({ size, variant }),
        "cc-ui-questionnaire-submit col-start-3 row-start-1 justify-self-end md:min-h-0",
        className
      )}
      {...props}
    >
      {children ?? ask.submit}
    </QuestionnairePrimitive.Submit>
  )
}

export {
  Questionnaire,
  QuestionnaireActions,
  QuestionnaireChoice,
  QuestionnaireChoiceDescription,
  QuestionnaireChoices,
  QuestionnaireDescription,
  QuestionnaireError,
  QuestionnaireInput,
  QuestionnaireItem,
  QuestionnaireNext,
  QuestionnairePrevious,
  QuestionnaireProgress,
  QuestionnaireSkip,
  QuestionnaireSubmit,
  QuestionnaireTitle,
}
