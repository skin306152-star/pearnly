// 引导式录入的界面预览（kit.html?page=guided&frame=menu|upload|card|review|result|collection，不带 frame 全部摆出来）。
// 只在开发环境，同展示页；全部用 components/ui 的标准组件拼，给评估 docs/work/2026-11-12-cc-guided-entry.md 截图用。
// 这里只有外观：没有接对话状态、没有接接口；施工时各块落到 components/ 下的业务组件里。
import type { ReactNode } from "react";
import { ChevronRightIcon, FileTextIcon, SearchIcon, ShoppingCartIcon, TagIcon, WalletIcon, XIcon } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Bubble, BubbleContent } from "@/components/ui/bubble";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput } from "@/components/ui/input-group";
import { Item, ItemActions, ItemContent, ItemDescription, ItemMedia, ItemTitle } from "@/components/ui/item";
import { Label } from "@/components/ui/label";
import { Message, MessageContent } from "@/components/ui/message";
import { NativeSelect, NativeSelectOption } from "@/components/ui/native-select";
import { Progress } from "@/components/ui/progress";
import {
  Questionnaire, QuestionnaireActions, QuestionnaireChoice, QuestionnaireChoiceDescription, QuestionnaireChoices, QuestionnaireItem,
  QuestionnaireNext, QuestionnaireProgress, QuestionnaireSkip, QuestionnaireSubmit, QuestionnaireTitle,
} from "@/components/ui/questionnaire";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Trace, TraceLine, TraceTitle } from "@/components/ui/trace";
import { co, guide, op, t } from "@/i18n";

import {
  BANK, COLLECTION_COMPANY, COLLECTION_LEDGER, COLLECTION_PERIOD, COMPANY, CUSTOMER, INVOICES, LEDGER, PERIOD, PURCHASE_LINES, RESULTS,
  SALES_LINES, SALES_TICKET, SLIP, TICKETS, WAREHOUSES, words,
} from "./guided-data";

const FRAMES = ["menu", "upload", "card", "review", "expand", "result", "collection"] as const;
type Frame = (typeof FRAMES)[number];

const where = (company: string, ledger: string, period: string) => `${company} · ${co.proposal.accountSet} ${ledger} · ${period}`;

function Frame({ name, children }: { name: Frame; children: ReactNode }) {
  return (
    <section data-guided={name} className="flex w-full flex-col gap-(--cc-space-12)">
      {children}
    </section>
  );
}

// 输入框上方的业务菜单：与「录吧」同一行、同一种按钮；选中的一项变实底并带「×」
function Menu({ chosen }: { chosen?: "purchase" }) {
  if (chosen) {
    return (
      <div className="flex flex-wrap gap-(--cc-space-8)">
        <Button variant="secondary"><ShoppingCartIcon />{op.group.purchase}<XIcon /></Button>
      </div>
    );
  }
  return (
    <div className="flex flex-wrap gap-(--cc-space-8)">
      <Button variant="outline"><ShoppingCartIcon />{op.group.purchase}</Button>
      <Button variant="outline"><TagIcon />{op.group.sales}</Button>
      <Button variant="outline"><WalletIcon />{guide.collection}</Button>
    </div>
  );
}

function RecordReply() {
  return (
    <div className="flex flex-wrap gap-(--cc-space-8)">
      <Button variant="outline">{t("recordNow")}</Button>
    </div>
  );
}

// 选了业务以后不出卡：输入框的占位文字换成按业务的一句，上传用输入框本来的入口（加号、拖入、粘贴）。
// 这里用标准输入件摆出占位文字的样子；产品里是 thread.tsx 的输入框
function Composer({ business }: { business: "purchase" | "collection" }) {
  return (
    <InputGroup>
      <InputGroupInput placeholder={guide.placeholder[business]} aria-label={guide.placeholder[business]} />
    </InputGroup>
  );
}

function Recognized({ lang }: { lang: string }) {
  return (
    <>
      <Message align="end"><MessageContent><Bubble><BubbleContent>{words(lang).files}</BubbleContent></Bubble></MessageContent></Message>
      <Trace><TraceLine><TraceTitle>{guide.recognized(12)}</TraceTitle></TraceLine></Trace>
    </>
  );
}

function GuideCard({ lang }: { lang: string }) {
  const w = words(lang);
  return (
    <Card className="px-(--cc-space-16)">
      <Questionnaire>
        <QuestionnaireProgress />
        <QuestionnaireItem name="payment">
          <QuestionnaireTitle>{w.question}</QuestionnaireTitle>
          <QuestionnaireChoices>
            <QuestionnaireChoice value="cash" defaultChecked>
              <span className="flex flex-wrap items-center gap-(--cc-space-8)">{w.cash}<Badge variant="secondary">{guide.recommended}</Badge></span>
              <QuestionnaireChoiceDescription>{w.cashWhy}</QuestionnaireChoiceDescription>
            </QuestionnaireChoice>
            <QuestionnaireChoice value="credit">
              {w.credit}
              <QuestionnaireChoiceDescription>{w.creditWhy}</QuestionnaireChoiceDescription>
            </QuestionnaireChoice>
          </QuestionnaireChoices>
        </QuestionnaireItem>
        <QuestionnaireItem name="again">
          <QuestionnaireTitle>{guide.already}</QuestionnaireTitle>
          <QuestionnaireChoices>
            <QuestionnaireChoice value="skip">{w.skipped}</QuestionnaireChoice>
            <QuestionnaireChoice value="again">{guide.entered}</QuestionnaireChoice>
          </QuestionnaireChoices>
        </QuestionnaireItem>
        <Collapsible>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm"><ChevronRightIcon />{guide.decided(6)}</Button>
          </CollapsibleTrigger>
        </Collapsible>
        <QuestionnaireActions>
          <QuestionnaireSkip />
          <QuestionnaireNext />
          <QuestionnaireSubmit />
        </QuestionnaireActions>
      </Questionnaire>
    </Card>
  );
}

function ReviewCard({ lang, phone }: { lang: string; phone: boolean }) {
  const w = words(lang);
  const kind = (k: string) => (k === "cash" ? w.kindCash : k === "credit" ? w.kindCredit : "");
  return (
    <Card>
      <CardHeader>
        <CardTitle>{guide.review}</CardTitle>
        <CardDescription>{where(COMPANY, LEDGER, PERIOD)}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-(--cc-space-8)">
        {phone ? (
          TICKETS.map((r) => (
            <Item key={r.id} variant="outline">
              <ItemMedia><Checkbox aria-label={r.number} defaultChecked={r.on} /></ItemMedia>
              <ItemContent>
                <ItemTitle>{r.party}</ItemTitle>
                <ItemDescription>{r.number} · {"already" in r ? `${guide.already} · ${w.skipped}` : kind(r.kind)}</ItemDescription>
              </ItemContent>
              <ItemActions>{r.total}</ItemActions>
            </Item>
          ))
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead />
                <TableHead>{guide.party}</TableHead>
                <TableHead>{guide.number}</TableHead>
                <TableHead>{guide.date}</TableHead>
                <TableHead>{guide.total}</TableHead>
                <TableHead>{guide.as}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {TICKETS.map((r) => (
                <TableRow key={r.id}>
                  <TableCell><Checkbox aria-label={r.number} defaultChecked={r.on} /></TableCell>
                  <TableCell>{r.party}</TableCell>
                  <TableCell><Input defaultValue={r.number} aria-invalid={"marked" in r} aria-label={guide.number} /></TableCell>
                  <TableCell>{r.date}</TableCell>
                  <TableCell><Input defaultValue={r.total} aria-label={guide.total} /></TableCell>
                  <TableCell>
                    {"already" in r ? <Badge variant="outline">{guide.already} · {w.skipped}</Badge> : kind(r.kind)}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <Collapsible>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" size="sm"><ChevronRightIcon />{guide.more(8)}</Button>
          </CollapsibleTrigger>
        </Collapsible>
      </CardContent>
    </Card>
  );
}

type Line = { id: string; item: string; qty: string; unit: string; price: string; warehouse: string; created?: boolean; cost?: boolean };

// 复核表里展开一张单：物料（可换成已有物料，账本里没有的标「新建」并露出名称 · 单位）、数量、单位、单价、仓库可改；
// 销售出库没有成本基础的行多一格单位成本。手机上每行一组字段
function Lines({ lines, phone }: { lines: readonly Line[]; phone: boolean }) {
  const warehouse = (line: Line) => (
    <NativeSelect defaultValue={line.warehouse} aria-label={guide.warehouse}>
      {WAREHOUSES.map((code) => <NativeSelectOption key={code} value={code}>{code}</NativeSelectOption>)}
    </NativeSelect>
  );
  const item = (line: Line) => (
    <InputGroup>
      <InputGroupAddon><SearchIcon /></InputGroupAddon>
      <InputGroupInput defaultValue={line.item} aria-label={guide.item} />
      {line.created ? <InputGroupAddon align="inline-end"><Badge variant="secondary">{guide.create}</Badge></InputGroupAddon> : null}
    </InputGroup>
  );
  if (phone) {
    return (
      <div className="flex flex-col gap-(--cc-space-16)">
        {lines.map((line) => (
          <FieldGroup key={line.id}>
            <Field><FieldLabel>{guide.item}</FieldLabel>{item(line)}</Field>
            <div className="grid grid-cols-2 gap-(--cc-space-8)">
              <Field><FieldLabel>{guide.quantity}</FieldLabel><Input defaultValue={line.qty} /></Field>
              <Field><FieldLabel>{guide.unit}</FieldLabel><Input defaultValue={line.unit} /></Field>
              <Field><FieldLabel>{guide.price}</FieldLabel><Input defaultValue={line.price} /></Field>
              <Field><FieldLabel>{guide.warehouse}</FieldLabel>{warehouse(line)}</Field>
              {line.cost ? <Field data-invalid="true"><FieldLabel>{guide.cost}</FieldLabel><Input aria-invalid /></Field> : null}
            </div>
          </FieldGroup>
        ))}
      </div>
    );
  }
  const cost = lines.some((line) => line.cost);
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{guide.item}</TableHead>
          <TableHead>{guide.quantity}</TableHead>
          <TableHead>{guide.unit}</TableHead>
          <TableHead>{guide.price}</TableHead>
          <TableHead>{guide.warehouse}</TableHead>
          {cost ? <TableHead>{guide.cost}</TableHead> : null}
        </TableRow>
      </TableHeader>
      <TableBody>
        {lines.map((line) => (
          <TableRow key={line.id}>
            <TableCell>{item(line)}</TableCell>
            <TableCell><Input defaultValue={line.qty} aria-label={guide.quantity} /></TableCell>
            <TableCell><Input defaultValue={line.unit} aria-label={guide.unit} /></TableCell>
            <TableCell><Input defaultValue={line.price} aria-label={guide.price} /></TableCell>
            <TableCell>{warehouse(line)}</TableCell>
            {cost ? <TableCell>{line.cost ? <Input aria-invalid aria-label={guide.cost} /> : null}</TableCell> : null}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

function ExpandedCard({ lang, phone }: { lang: string; phone: boolean }) {
  const w = words(lang);
  const ticket = TICKETS[2];
  return (
    <Card>
      <CardHeader>
        <CardTitle>{guide.review}</CardTitle>
        <CardDescription>{where(COMPANY, LEDGER, PERIOD)}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-(--cc-space-12)">
        <Item variant="outline">
          <ItemMedia><Checkbox aria-label={ticket.number} defaultChecked /></ItemMedia>
          <ItemContent>
            <ItemTitle>{ticket.party}</ItemTitle>
            <ItemDescription>{ticket.number} · {ticket.date} · {w.kindCredit}</ItemDescription>
          </ItemContent>
          <ItemActions>{ticket.total}</ItemActions>
        </Item>
        <Lines lines={PURCHASE_LINES} phone={phone} />
        <Item variant="outline">
          <ItemMedia><Checkbox aria-label={SALES_TICKET.number} defaultChecked /></ItemMedia>
          <ItemContent>
            <ItemTitle>{SALES_TICKET.party}</ItemTitle>
            <ItemDescription>{SALES_TICKET.number} · {op.group.sales}</ItemDescription>
          </ItemContent>
          <ItemActions>{SALES_TICKET.total}</ItemActions>
        </Item>
        <Lines lines={SALES_LINES} phone={phone} />
      </CardContent>
    </Card>
  );
}

function ResultCard({ lang }: { lang: string }) {
  const w = words(lang);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{guide.result(10, 1)}</CardTitle>
        <CardDescription>{where(COMPANY, LEDGER, PERIOD)}</CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-(--cc-space-8)">
        <Progress value={100} />
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{guide.party}</TableHead>
              <TableHead>{guide.number}</TableHead>
              <TableHead>{guide.ledgerNumber}</TableHead>
              <TableHead>{guide.total}</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {RESULTS.map((r) => (
              <TableRow key={r.id}>
                <TableCell>{r.party}</TableCell>
                <TableCell>{r.number}</TableCell>
                <TableCell>{r.ledger}</TableCell>
                <TableCell>{r.total}</TableCell>
                <TableCell>
                  {r.ok ? <Badge variant="secondary">{guide.entered}</Badge> : <Badge variant="destructive">{w.outside}</Badge>}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

function CollectionCard() {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{guide.collection}</CardTitle>
        <CardDescription>{where(COLLECTION_COMPANY, COLLECTION_LEDGER, COLLECTION_PERIOD)}</CardDescription>
      </CardHeader>
      <CardContent>
        <FieldGroup>
          <Field>
            <FieldLabel htmlFor="guided-customer">{guide.customer}</FieldLabel>
            <InputGroup>
              <InputGroupAddon><SearchIcon /></InputGroupAddon>
              <InputGroupInput id="guided-customer" defaultValue={CUSTOMER} />
            </InputGroup>
          </Field>
          <Field>
            <FieldLabel>{guide.openInvoices}</FieldLabel>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead />
                  <TableHead>{guide.number}</TableHead>
                  <TableHead>{guide.date}</TableHead>
                  <TableHead>{guide.due}</TableHead>
                  <TableHead>{guide.receive}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {INVOICES.map((r) => (
                  <TableRow key={r.id}>
                    <TableCell><Checkbox aria-label={r.number} defaultChecked={r.on} /></TableCell>
                    <TableCell>{r.number}</TableCell>
                    <TableCell>{r.date}</TableCell>
                    <TableCell>{r.due}</TableCell>
                    <TableCell>{r.on ? <Input defaultValue={r.receive} aria-label={guide.receive} /> : null}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Field>
          <Field>
            <FieldLabel>{guide.method}</FieldLabel>
            <RadioGroup defaultValue="transfer" className="flex flex-wrap gap-(--cc-space-16)">
              <Label><RadioGroupItem value="cash" />{guide.cash}</Label>
              <Label><RadioGroupItem value="transfer" />{guide.transfer} {BANK}</Label>
            </RadioGroup>
            <Item variant="outline">
              <ItemMedia variant="icon"><FileTextIcon /></ItemMedia>
              <ItemContent>
                <ItemTitle>{guide.slip}</ItemTitle>
                <ItemDescription>{SLIP}</ItemDescription>
              </ItemContent>
              <ItemActions><Button size="sm" variant="outline">{t("edit")}</Button></ItemActions>
            </Item>
          </Field>
          <div className="flex items-center justify-between font-medium">
            <span>{guide.total}</span>
            <span>23,500.00</span>
          </div>
        </FieldGroup>
      </CardContent>
    </Card>
  );
}

export function Guided({ lang, frame }: { lang: string; frame: string }) {
  const phone = typeof window !== "undefined" && window.innerWidth < 768;
  const shown = (FRAMES as readonly string[]).includes(frame) ? [frame as Frame] : [...FRAMES];
  const w = words(lang);
  return (
    <main className="mx-auto flex max-w-(--cc-thread-w) flex-col gap-(--cc-space-40) p-(--cc-space-16)">
      {shown.includes("menu") && <Frame name="menu"><Menu /></Frame>}
      {shown.includes("upload") && <Frame name="upload"><Menu chosen="purchase" /><Composer business="purchase" /></Frame>}
      {shown.includes("card") && <Frame name="card"><Recognized lang={lang} /><GuideCard lang={lang} /></Frame>}
      {shown.includes("review") && (
        <Frame name="review">
          <ReviewCard lang={lang} phone={phone} />
          <Message><MessageContent>{w.restate}</MessageContent></Message>
          <RecordReply />
        </Frame>
      )}
      {shown.includes("expand") && <Frame name="expand"><ExpandedCard lang={lang} phone={phone} /></Frame>}
      {shown.includes("result") && <Frame name="result"><ResultCard lang={lang} /><Menu /></Frame>}
      {shown.includes("collection") && (
        <Frame name="collection">
          <CollectionCard />
          <Message><MessageContent>{w.restateCollection}</MessageContent></Message>
          <RecordReply />
        </Frame>
      )}
    </main>
  );
}
