// 标准组件库展示页（只在开发环境：vite 开发服务器，或构建时设 CC_KIT=1；生产构建没有它，docs/development/cc-ui-kit.md）。
// 每个组件摆几种状态；字一律取自现有词条，切语言就能看四语。标题只写组件名（开发用页，不进产品文案）。
import { useState, type ReactNode } from "react";
import { ArrowBigUpIcon, CommandIcon, CopyIcon, CornerDownLeftIcon, FileTextIcon, InboxIcon, MoonIcon, SearchIcon, SunIcon, TriangleAlertIcon, WrenchIcon, XIcon } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  Attachment, AttachmentAction, AttachmentActions, AttachmentContent, AttachmentDescription, AttachmentGroup, AttachmentMedia, AttachmentTitle,
} from "@/components/ui/attachment";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Button } from "@/components/ui/button";
import { ButtonGroup } from "@/components/ui/button-group";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { CodeBlock, CodeBlockActions, CodeBlockContent, CodeBlockCopyButton, CodeBlockHeader, CodeBlockTitle } from "@/components/ui/code-block";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator,
  DropdownMenuShortcut, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Field, FieldDescription, FieldError, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Item, ItemActions, ItemContent, ItemDescription, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Kbd, KbdGroup } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { Marker, MarkerContent } from "@/components/ui/marker";
import { Message, MessageContent, MessageGroup } from "@/components/ui/message";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Popover, PopoverContent, PopoverDescription, PopoverHeader, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { Progress } from "@/components/ui/progress";
import {
  Questionnaire, QuestionnaireActions, QuestionnaireChoice, QuestionnaireChoices, QuestionnaireItem, QuestionnaireNext,
  QuestionnairePrevious, QuestionnaireProgress, QuestionnaireSkip, QuestionnaireSubmit, QuestionnaireTitle,
} from "@/components/ui/questionnaire";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Switch } from "@/components/ui/switch";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Toggle } from "@/components/ui/toggle";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { Trace, TraceCollapsible, TraceCollapsibleContent, TraceCollapsibleTrigger, TraceIcon, TraceLine, TraceTitle } from "@/components/ui/trace";
import { ask, st, t, ui } from "@/i18n";

// 示例数据也只用现有词条（展示页不写死字，文案闸门同产品页一样扫这里）
const file = (n: number) => t("attachment").replace("{0}", String(n));

const LANGS = ["th", "zh", "en", "ja"] as const;

function go(change: Record<string, string>) {
  const url = new URL(location.href);
  for (const [k, v] of Object.entries(change)) url.searchParams.set(k, v);
  location.href = url.toString();
}

function Section({ name, children }: { name: string; children: ReactNode }) {
  return (
    <section data-kit={name} className="flex flex-col gap-(--cc-space-12)">
      <h2 className="text-subhead font-medium text-ink">{name}</h2>
      <div className="flex flex-wrap items-start gap-(--cc-space-12)">{children}</div>
    </section>
  );
}

function Toolbar({ lang, dark }: { lang: string; dark: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-(--cc-space-12)">
      <ToggleGroup type="single" value={lang} onValueChange={(v) => v && go({ lang: v })}>
        {LANGS.map((l) => (
          <ToggleGroupItem key={l} value={l}>{l}</ToggleGroupItem>
        ))}
      </ToggleGroup>
      <ToggleGroup type="single" value={dark ? "dark" : "light"} onValueChange={(v) => v && go({ theme: v })}>
        <ToggleGroupItem value="light" aria-label={st.themeLight}><SunIcon /></ToggleGroupItem>
        <ToggleGroupItem value="dark" aria-label={st.themeDark}><MoonIcon /></ToggleGroupItem>
      </ToggleGroup>
    </div>
  );
}

export function Gallery({ lang, dark }: { lang: string; dark: boolean }) {
  const [on, setOn] = useState(true);
  const [checked, setChecked] = useState(true);
  return (
    <TooltipProvider>
      <main className="mx-auto flex max-w-(--cc-dialog-w-lg) flex-col gap-(--cc-space-28) p-(--cc-space-16)">
        <Toolbar lang={lang} dark={dark} />

        <Section name="Button">
          <Button>{t("save")}</Button>
          <Button variant="outline">{t("cancel")}</Button>
          <Button variant="secondary">{t("edit")}</Button>
          <Button variant="ghost">{t("more")}</Button>
          <Button variant="destructive">{t("delete")}</Button>
          <Button variant="link">{t("learnMore")}</Button>
          <Button size="sm" variant="outline">{t("rename")}</Button>
          <Button size="xs">{ask.submit}</Button>
          <Button size="xs" variant="secondary">{ask.skip}</Button>
          <Button size="icon" variant="ghost" aria-label={t("search")}><SearchIcon /></Button>
          <Button size="icon-sm" variant="ghost" aria-label={t("copy")}><CopyIcon /></Button>
          <Button disabled>{t("send")}</Button>
          <ButtonGroup>
            <Button variant="outline">{t("previous")}</Button>
            <Button variant="outline">{t("next")}</Button>
          </ButtonGroup>
        </Section>

        <Section name="Toggle / ToggleGroup">
          <Toggle aria-label={t("star")} defaultPressed>{t("star")}</Toggle>
          <ToggleGroup type="single" defaultValue="a">
            <ToggleGroupItem value="a">{t("today")}</ToggleGroupItem>
            <ToggleGroupItem value="b">{t("yesterday")}</ToggleGroupItem>
          </ToggleGroup>
        </Section>

        <Section name="Badge / Kbd / Avatar / Spinner / Skeleton">
          <Badge>{t("running")}</Badge>
          <Badge variant="secondary">{t("uploading")}</Badge>
          <Badge variant="destructive">{t("uploadFailed")}</Badge>
          <Badge variant="outline">{t("question")}</Badge>
          <KbdGroup><Kbd><CommandIcon /></Kbd><Kbd><ArrowBigUpIcon /></Kbd><Kbd><CornerDownLeftIcon /></Kbd></KbdGroup>
          <Avatar><AvatarFallback>CC</AvatarFallback></Avatar>
          <Spinner />
          <Skeleton className="h-(--cc-control) w-(--cc-attach-w)" />
        </Section>

        <Section name="Input / Textarea / Select / NativeSelect">
          <FieldGroup className="w-full max-w-(--cc-dialog-w-sm)">
            <Field>
              <FieldLabel htmlFor="kit-input">{t("rename")}</FieldLabel>
              <Input id="kit-input" placeholder={t("search")} />
              <FieldDescription>{t("noResults")}</FieldDescription>
            </Field>
            <Field data-invalid="true">
              <FieldLabel htmlFor="kit-bad">{t("edit")}</FieldLabel>
              <Input id="kit-bad" aria-invalid="true" defaultValue="0315551000297" />
              <FieldError>{t("somethingWrong")}</FieldError>
            </Field>
            <InputGroup>
              <InputGroupAddon><SearchIcon /></InputGroupAddon>
              <InputGroupInput placeholder={t("search")} />
            </InputGroup>
            <Textarea placeholder={t("placeholder")} />
            <Select defaultValue="th">
              <SelectTrigger aria-label={t("language")}><SelectValue /></SelectTrigger>
              <SelectContent>
                {LANGS.map((l) => <SelectItem key={l} value={l}>{l}</SelectItem>)}
              </SelectContent>
            </Select>
            <NativeSelect aria-label={t("language")} defaultValue="zh">
              {LANGS.map((l) => <NativeSelectOption key={l} value={l}>{l}</NativeSelectOption>)}
            </NativeSelect>
          </FieldGroup>
        </Section>

        <Section name="Checkbox / RadioGroup / Switch / Label">
          <Label><Checkbox checked={checked} onCheckedChange={(v) => setChecked(v === true)} />{t("star")}</Label>
          <Label><Checkbox disabled />{t("archive")}</Label>
          <RadioGroup defaultValue="a">
            <Label><RadioGroupItem value="a" />{t("today")}</Label>
            <Label><RadioGroupItem value="b" />{t("yesterday")}</Label>
          </RadioGroup>
          <Label><Switch checked={on} onCheckedChange={setOn} />{t("settings")}</Label>
        </Section>

        <Section name="Tabs">
          <Tabs defaultValue="preview" className="w-full">
            <TabsList>
              <TabsTrigger value="preview">{t("preview")}</TabsTrigger>
              <TabsTrigger value="source">{t("source")}</TabsTrigger>
            </TabsList>
            <TabsContent value="preview" className="text-body text-ink-2">{t("noPreview")}</TabsContent>
            <TabsContent value="source" className="text-body text-ink-2">{t("source")}</TabsContent>
          </Tabs>
        </Section>

        <Section name="Table">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead><Checkbox aria-label={t("star")} defaultChecked /></TableHead>
                <TableHead>{t("files")}</TableHead>
                <TableHead>{t("pageCount").replace("{0}", "")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {[file(1), file(2), file(3)].map((name, i) => (
                <TableRow key={name} data-state={i === 0 ? "selected" : undefined}>
                  <TableCell><Checkbox aria-label={name} defaultChecked={i !== 2} /></TableCell>
                  <TableCell>{name}</TableCell>
                  <TableCell>{i === 2 ? <Badge variant="destructive">{t("uploadFailed")}</Badge> : i + 1}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Section>

        <Section name="Card / Item / Empty / Alert / Progress">
          <Card className="w-(--cc-dialog-w-sm) max-w-full">
            <CardHeader>
              <CardTitle>{t("scheduledTasks")}</CardTitle>
              <CardDescription>{t("today")}</CardDescription>
            </CardHeader>
            <CardContent><Progress value={60} /></CardContent>
            <CardFooter><Button size="xs" variant="secondary">{t("cancel")}</Button><Button size="xs">{t("save")}</Button></CardFooter>
          </Card>
          <Item variant="outline" className="max-w-(--cc-dialog-w-sm)">
            <ItemMedia variant="icon"><FileTextIcon /></ItemMedia>
            <ItemContent>
              <ItemTitle>{file(1)}</ItemTitle>
              <ItemDescription>{t("onePage")}</ItemDescription>
            </ItemContent>
            <ItemActions><Button size="sm" variant="outline">{t("download")}</Button></ItemActions>
          </Item>
          <Empty className="max-w-(--cc-dialog-w-sm)">
            <EmptyHeader>
              <EmptyMedia><InboxIcon /></EmptyMedia>
              <EmptyTitle>{t("noResults")}</EmptyTitle>
              <EmptyDescription>{t("search")}</EmptyDescription>
            </EmptyHeader>
          </Empty>
          <Alert variant="destructive" className="max-w-(--cc-dialog-w-sm)">
            <TriangleAlertIcon />
            <AlertTitle>{t("uploadFailed")}</AlertTitle>
            <AlertDescription>{t("somethingWrong")}</AlertDescription>
          </Alert>
          <Alert className="max-w-(--cc-dialog-w-sm)">
            <AlertTitle>{t("notice")}</AlertTitle>
            <AlertDescription>{t("newNotice")}</AlertDescription>
          </Alert>
        </Section>

        <Section name="Dialog / AlertDialog / Sheet / Popover / DropdownMenu / Tooltip">
          <Dialog>
            <DialogTrigger asChild><Button variant="outline">{t("rename")}</Button></DialogTrigger>
            <DialogContent className="max-w-(--cc-dialog-w-sm)">
              <DialogHeader>
                <DialogTitle>{t("rename")}</DialogTitle>
                <DialogDescription>{t("untitled")}</DialogDescription>
              </DialogHeader>
              <Input defaultValue={t("untitled")} aria-label={t("rename")} />
              <DialogFooter><Button variant="outline">{t("cancel")}</Button><Button>{t("save")}</Button></DialogFooter>
            </DialogContent>
          </Dialog>
          <AlertDialog>
            <AlertDialogTrigger asChild><Button variant="destructive">{t("delete")}</Button></AlertDialogTrigger>
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{t("delete")}</AlertDialogTitle>
                <AlertDialogDescription>{file(1)}</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel>{t("cancel")}</AlertDialogCancel>
                <AlertDialogAction variant="destructive">{t("delete")}</AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
          <Sheet>
            <SheetTrigger asChild><Button variant="outline">{t("files")}</Button></SheetTrigger>
            <SheetContent>
              <SheetHeader>
                <SheetTitle>{t("files")}</SheetTitle>
                <SheetDescription>{t("onePage")}</SheetDescription>
              </SheetHeader>
            </SheetContent>
          </Sheet>
          <Popover>
            <PopoverTrigger asChild><Button variant="outline">{t("notice")}</Button></PopoverTrigger>
            <PopoverContent>
              <PopoverHeader>
                <PopoverTitle>{t("notice")}</PopoverTitle>
                <PopoverDescription>{t("newNotice")}</PopoverDescription>
              </PopoverHeader>
            </PopoverContent>
          </Popover>
          <DropdownMenu>
            <DropdownMenuTrigger asChild><Button variant="outline">{t("more")}</Button></DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuLabel>{t("conversationMenu")}</DropdownMenuLabel>
              <DropdownMenuItem>{t("rename")}<DropdownMenuShortcut><CommandIcon /></DropdownMenuShortcut></DropdownMenuItem>
              <DropdownMenuCheckboxItem checked>{t("star")}</DropdownMenuCheckboxItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive">{t("delete")}</DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <Tooltip>
            <TooltipTrigger asChild><Button variant="outline">{t("copy")}</Button></TooltipTrigger>
            <TooltipContent>{t("copy")}</TooltipContent>
          </Tooltip>
        </Section>

        <Section name="Message / Bubble / Marker">
          <MessageGroup className="w-full">
            <Message align="end">
              <MessageContent>
                <Bubble align="end"><BubbleContent>{t("placeholder")}</BubbleContent></Bubble>
              </MessageContent>
            </Message>
            <Message>
              <MessageContent>
                <Bubble variant="ghost"><BubbleContent>{t("morning")}</BubbleContent></Bubble>
              </MessageContent>
            </Message>
            <Marker variant="separator"><MarkerContent>{t("today")}</MarkerContent></Marker>
          </MessageGroup>
        </Section>

        <Section name="Attachment">
          <AttachmentGroup>
            <Attachment>
              <AttachmentMedia><FileTextIcon /></AttachmentMedia>
              <AttachmentContent>
                <AttachmentTitle>{file(1)}</AttachmentTitle>
                <AttachmentDescription>{t("onePage")}</AttachmentDescription>
              </AttachmentContent>
              <AttachmentActions><AttachmentAction aria-label={t("removeFile")}><XIcon /></AttachmentAction></AttachmentActions>
            </Attachment>
          </AttachmentGroup>
        </Section>

        <Section name="Trace">
          <Trace>
            <TraceLine><TraceIcon><WrenchIcon /></TraceIcon><TraceTitle>{t("tools")}</TraceTitle></TraceLine>
            <TraceLine status="running"><TraceIcon><Spinner /></TraceIcon><TraceTitle>{t("uploading")}</TraceTitle></TraceLine>
            <TraceCollapsible>
              <TraceCollapsibleTrigger>{t("moreItems").replace("{0}", "2")}</TraceCollapsibleTrigger>
              <TraceCollapsibleContent>{file(1)} · {file(2)}</TraceCollapsibleContent>
            </TraceCollapsible>
          </Trace>
        </Section>

        <Section name="Questionnaire">
          <Card className="w-(--cc-dialog-w-sm) max-w-full px-(--cc-space-16)">
            <Questionnaire>
              <QuestionnaireProgress />
              <QuestionnaireItem name="when">
                <QuestionnaireTitle>{t("question")}</QuestionnaireTitle>
                <QuestionnaireChoices>
                  <QuestionnaireChoice value="today">{t("today")}</QuestionnaireChoice>
                  <QuestionnaireChoice value="yesterday">{t("yesterday")}</QuestionnaireChoice>
                </QuestionnaireChoices>
              </QuestionnaireItem>
              <QuestionnaireItem name="what" multiple>
                <QuestionnaireTitle>{t("files")}</QuestionnaireTitle>
                <QuestionnaireChoices>
                  <QuestionnaireChoice value="a">{file(1)}</QuestionnaireChoice>
                  <QuestionnaireChoice value="b">{file(2)}</QuestionnaireChoice>
                </QuestionnaireChoices>
              </QuestionnaireItem>
              <QuestionnaireActions>
                <QuestionnairePrevious />
                <QuestionnaireSkip />
                <QuestionnaireNext />
                <QuestionnaireSubmit />
              </QuestionnaireActions>
            </Questionnaire>
          </Card>
        </Section>

        <Section name="CodeBlock / Separator">
          <CodeBlock className="w-full">
            <CodeBlockHeader>
              <CodeBlockTitle>{t("source")}</CodeBlockTitle>
              <CodeBlockActions><CodeBlockCopyButton value={'{ "copied": "' + ui.copied + '" }'} /></CodeBlockActions>
            </CodeBlockHeader>
            <CodeBlockContent>{'{ "copied": "' + ui.copied + '" }'}</CodeBlockContent>
          </CodeBlock>
          <Separator />
        </Section>
      </main>
    </TooltipProvider>
  );
}
