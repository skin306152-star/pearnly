"""OCR schemas · 各层结果包装(Layer2/Layer3/Pipeline 页结果与汇总)(REFACTOR-WA · R20 拆 · 0 逻辑改)。"""
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field
from pearnly_cowork.ocr.schema.evidence import BusinessDocumentType
from pearnly_cowork.ocr.schema.invoice import ThaiInvoice

class Layer2PageResult(BaseModel):
    """Layer2PageResult: invoice/auto results from ec2e5c957e6b, with raw provenance retained."""
    page_number: int = Field(..., ge=1, description='1-based, matches Layer1.Page.page_number')
    invoice: ThaiInvoice
    document_type: BusinessDocumentType = Field(default='invoice', description='business-entry document type (passed through from entry)')
    document: None = Field(default=None, description='Reserved: this slice accepts invoice/auto only.')
    elapsed_ms: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0, description='number of JSON-parse retries actually consumed (0 = first call OK)')
    skipped: bool = Field(default=False, description='true when page text was empty / too short and no API call was made')
    validation_warnings: List[str] = Field(default_factory=list, description="warnings from validators (e.g. 'amount 6091 sourced from description column — rejected')")

class Layer2Result(BaseModel):
    """Layer2Result: invoice/auto results from ec2e5c957e6b, with raw provenance retained."""
    pages: List[Layer2PageResult]
    elapsed_ms: int = Field(default=0, ge=0, description='wall-clock for all pages')
    model: str = Field(default='', description='Gemini model identifier used')
    engine: str = Field(default='layer2_structure', description='for cost log / audit')

class Layer3PageResult(BaseModel):
    """Layer3PageResult: invoice/auto results from ec2e5c957e6b, with raw provenance retained."""
    page_number: int = Field(..., ge=1, description='1-based, matches Layer1.Page.page_number')
    invoice: ThaiInvoice = Field(..., description='corrected ThaiInvoice after visual refinement')
    elapsed_ms: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0, description='includes both text + image tokens')
    output_tokens: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0, description='JSON-parse retries actually consumed (0 = first call OK)')
    trigger_reasons: List[str] = Field(default_factory=list, description='why pipeline.py called layer 3 (passed back in result for audit)')
    model: str = Field(default='', description='Gemini model identifier used')

class PipelinePageResult(BaseModel):
    """PipelinePageResult: invoice/auto results from ec2e5c957e6b, with raw provenance retained."""
    page_number: int = Field(..., ge=1)
    extraction_audit: Dict[str, Any] = Field(default_factory=dict, description='local reconstruction provenance and inferred-value audit; never sent as a prompt')
    invoice: ThaiInvoice = Field(..., description='final invoice, from L2 or L3')
    document_type: BusinessDocumentType = Field(default='invoice')
    document: None = Field(default=None, description='Reserved: this slice accepts invoice/auto only.')
    layer_chain: List[str] = Field(default_factory=list, description='which layers ran, e.g. ["L1","L2"] or ["L1","L2","L3"] or ["L1","L2","L3_failed"] (L3 was tried but failed; L2 result kept)')
    trigger_reasons: List[str] = Field(default_factory=list, description='why L3 was called (empty list = L3 not invoked)')
    layer1_avg_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    layer2_input_tokens: int = Field(default=0, ge=0)
    layer2_output_tokens: int = Field(default=0, ge=0)
    layer3_input_tokens: int = Field(default=0, ge=0)
    layer3_output_tokens: int = Field(default=0, ge=0)
    layer2_model: str = Field(default='', description='L2 实际用的模型名(成本按它计价)')
    layer3_model: str = Field(default='', description='L3/升级臂实际用的模型名;没跑 = 空')
    layer1_ms: int = Field(default=0, ge=0)
    layer2_ms: int = Field(default=0, ge=0)
    layer3_ms: int = Field(default=0, ge=0, description="0 if L3 didn't run")
    total_ms: int = Field(default=0, ge=0)
    needs_manual_review: bool = Field(default=False, description='true when L3 was triggered but failed (and we kept L2 result), OR (future) when final confidence is too low for auto-acceptance')
    declared_direction: str = Field(default='', description='人已裁决的进项/销项(回导时 = 行在哪张 Sheet);空 = 没人裁决过,下游照常按税号锚点判')
    source_history_id: str = Field(default='', description='本条替换的原 ocr_history id(回导行键带回)。防重单闸靠它找上一版 ERP 凭证号 —— 收料口会为回导文件新建记录,拿新 id 查必然查不到')
    confidence_band: Literal['auto', 'yellow_confirm', 'needs_review'] = Field(default='auto', description='confidence routing bucket: auto (>=0.98 — can flow into ERP) / yellow_confirm (0.90-0.98 — user must click confirm) / needs_review (<0.90 — into manual review queue)')
    final_confidence: float = Field(default=0.0, ge=0.0, le=1.0, description='aggregate page confidence (min of critical-field confidences)')
    field_confidence: Dict[str, float] = Field(default_factory=dict, description="per-critical-field min L1 word confidence (invoice_number / total_amount / seller_tax / date) — feeds the review screen 'needs-check' field highlight; field absent from OCR text (hallucinated/missing) → 0.0")
    validation_warnings: List[str] = Field(default_factory=list, description='warnings from validators (mis-sourced amount, etc.)')
    error: Optional[str] = Field(default=None, description='set if any layer hit a recoverable error (caller can still use invoice)')

class PipelineResult(BaseModel):
    """PipelineResult: invoice/auto results from ec2e5c957e6b, with raw provenance retained."""
    pages: List[PipelinePageResult]
    page_count: int = Field(..., ge=0)
    elapsed_ms: int = Field(..., ge=0)
    engine: str = Field(default='pipeline_v1')
    estimated_cost_thb: Decimal = Field(default=Decimal('0'), ge=Decimal('0'), allow_inf_nan=False, description='rough cost estimate in THB based on Vision pages + Gemini tokens')
