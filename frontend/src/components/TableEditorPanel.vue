<template>
  <section class="glass-strong" style="padding: 20px; margin-bottom: 24px">
    <!-- 顶部状态与多模式导出栏 -->
    <div
      style="
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 14px;
      "
    >
      <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
        <span class="gem-pill" :class="activeQuotation?.is_demo ? 'pill-gold' : 'pill-emerald'">
          {{ activeQuotation?.is_demo ? "演示提取模式" : `AI 引擎：${activeQuotation?.engine || "gemini"}` }}
        </span>
        <span
          v-if="activeQuotation"
          class="gem-pill"
          :class="activeQuotation.detected_doc_type === 'general' ? 'pill-sky' : 'pill-violet'"
        >
          {{ activeQuotation.detected_doc_type === "general" ? "通用表格 · 商务灰蓝" : "报价单模式 · 苹果绿" }}
        </span>
        <span v-if="activeQuotation?.notice" class="glass-muted" style="font-size: 13px">
          {{ activeQuotation.notice }}
        </span>
      </div>

      <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
        <el-button
          type="primary"
          size="small"
          :loading="exportingKey === 'current_xlsx'"
          @click="exportCurrentXlsx"
        >
          导出当前表格 (.xlsx)
        </el-button>
        <el-button
          v-if="quotations.length > 1"
          type="success"
          size="small"
          :loading="exportingKey === 'batch_zip'"
          @click="exportAllZip"
        >
          批量打包全部 (.zip · {{ quotations.length }} 份)
        </el-button>
        <el-button
          v-if="quotations.length > 1"
          size="small"
          :loading="exportingKey === 'all_in_one_xlsx'"
          @click="exportAllInOneXlsx"
        >
          合并为多 Sheet (.xlsx)
        </el-button>
        <el-button size="small" :loading="exportingKey === 'csv'" @click="exportCsv">
          导出 .csv
        </el-button>
        <el-button size="small" @click="copyMarkdown">复制 Markdown</el-button>
      </div>
    </div>

    <!-- 多报价单图片切换栏（1 图 = 1 份报价单） -->
    <div
      style="
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        margin-bottom: 14px;
        padding: 10px 12px;
        border-radius: var(--glass-radius-sm);
        background: var(--glass-bg-input);
        border: 1px solid var(--glass-border);
      "
    >
      <div v-auto-animate style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap">
        <span class="glass-muted" style="font-size: 12.5px; font-weight: 600; margin-right: 4px">
          文档列表 ({{ quotations.length }})：
        </span>
        <button
          v-for="(q, idx) in quotations"
          :key="`${idx}-${q.source_filename}`"
          type="button"
          class="nav-pill-btn"
          :class="{ 'is-active': activeQuoteIdx === idx }"
          @click="selectQuotation(idx)"
        >
          {{ q.quote_title || q.source_filename }}
        </button>
      </div>

      <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
        <div style="display: flex; align-items: center; gap: 6px">
          <span class="glass-muted" style="font-size: 12px">导出文件名:</span>
          <el-input
            v-if="activeQuotation"
            :model-value="activeQuotation.quote_title"
            size="small"
            style="width: 190px"
            placeholder="输入导出名称"
            @update:model-value="updateQuoteTitle"
          />
        </div>
        <el-switch
          :model-value="mergePerImage"
          active-text="合为单表"
          inactive-text="拆分子表"
          inline-prompt
          @update:model-value="(val: boolean) => $emit('update:mergePerImage', val)"
        />
      </div>
    </div>

    <!-- Bento KPI 概览 -->
    <div class="bento-grid">
      <SpotlightCard>
        <div class="kpi-label">本批文档数</div>
        <div class="kpi-value num-tabular">{{ quotations.length }}</div>
        <span class="gem-pill pill-sky">{{ activeQuotation?.detected_doc_type === "general" ? "多场景通用表" : "1 图 = 1 完整表" }}</span>
      </SpotlightCard>
      <SpotlightCard>
        <div class="kpi-label">当前表行数</div>
        <div class="kpi-value num-tabular">{{ currentTotalRows }}</div>
        <span class="gem-pill pill-emerald">零丢行对齐</span>
      </SpotlightCard>
      <SpotlightCard>
        <div class="kpi-label">当前表列数</div>
        <div class="kpi-value num-tabular">{{ activeSheet?.headers.length ?? 0 }}</div>
        <span class="gem-pill pill-violet">支持类型校对</span>
      </SpotlightCard>
      <SpotlightCard>
        <div class="kpi-label">待核对提示格</div>
        <div class="kpi-value num-tabular">{{ activeSheet?.warnings.length ?? 0 }}</div>
        <span class="gem-pill" :class="(activeSheet?.warnings.length ?? 0) > 0 ? 'pill-gold' : 'pill-emerald'">
          {{ (activeSheet?.warnings.length ?? 0) > 0 ? "已高亮标黄" : "全部校验通过" }}
        </span>
      </SpotlightCard>
    </div>

    <!-- 商家顶部规则备注提示条（Q5：独立展示，不掺入表格数据行） -->
    <div
      v-if="activeQuotation?.document_notes"
      class="glass"
      style="
        padding: 10px 14px;
        margin-bottom: 14px;
        border-left: 3px solid var(--glass-accent);
        font-size: 13px;
        line-height: 1.6;
      "
    >
      <span style="font-weight: 700; margin-right: 6px">商家规则 / 备注说明：</span>
      <span class="glass-muted">{{ activeQuotation.document_notes }}</span>
    </div>

    <!-- 拆分子表选择器（仅在关闭合并单表且原图含多个区块时显示） + 表格编辑栏 -->
    <div
      style="
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        margin-bottom: 14px;
      "
    >
      <div v-auto-animate style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap">
        <template v-if="!mergePerImage && currentSheets.length > 1">
          <button
            v-for="(sh, idx) in currentSheets"
            :key="`${idx}-${sh.name}`"
            type="button"
            class="nav-pill-btn"
            :class="{ 'is-active': activeSubSheetIdx === idx }"
            @click="activeSubSheetIdx = idx"
          >
            {{ sh.name || `区块${idx + 1}` }}
          </button>
        </template>
        <span v-else class="gem-pill pill-violet">
          {{ mergePerImage && (activeQuotation?.raw_sheets?.length || 0) > 1
            ? `已自动合并 ${activeQuotation?.raw_sheets?.length} 个区块为单 Sheet`
            : activeSheet?.name || "Sheet1" }}
        </span>
      </div>

      <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
        <el-button
          v-if="hasModelAndCodeCols"
          size="small"
          type="success"
          plain
          :loading="importingDict"
          title="将当前表格中已核对无误的「机型 ↔ 网络型号」批量写入云端记忆字典，下次遇到歧义自动消歧"
          @click="importCurrentSheetToDict"
        >
          收录当前表到字典
        </el-button>
        <el-button
          v-if="hasModelAndCodeCols && quotations.length > 1"
          size="small"
          type="primary"
          plain
          :loading="importingDict"
          title="一次性将本批全部已加载表格的无误机型映射收录进云端字典"
          @click="importAllSheetsToDict"
        >
          收录全部表 ({{ quotations.length }})
        </el-button>
        <el-button size="small" @click="showPreview = !showPreview">
          {{ showPreview ? "隐藏原图对照" : "显示原图对照" }}
        </el-button>
        <el-button size="small" @click="addRow">+ 新增行</el-button>
        <el-button size="small" @click="addColumn">+ 新增列</el-button>
        <el-button size="small" type="danger" plain @click="$emit('clear-draft')">关闭工作台</el-button>
      </div>
    </div>

    <!-- 分栏对照主区域：左原图（支持最高 300% 放大看长报价单）+ 右可编辑表格 -->
    <div :class="showPreview ? 'editor-split' : ''" style="gap: 18px">
      <div v-if="showPreview" class="glass image-preview-pane">
        <div
          style="
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            margin-bottom: 10px;
          "
        >
          <span
            style="
              font-weight: 700;
              font-size: 13px;
              color: var(--glass-text);
              overflow: hidden;
              text-overflow: ellipsis;
              white-space: nowrap;
            "
          >
            原图：{{ activeQuotation?.source_filename || "当前报价单" }}
          </span>
          <div style="display: flex; align-items: center; gap: 4px; flex-shrink: 0">
            <el-button size="small" @click="zoom = Math.max(50, zoom - 25)">-</el-button>
            <span
              class="num-tabular"
              style="font-size: 12px; min-width: 44px; text-align: center; cursor: pointer"
              title="点击恢复 100%"
              @click="zoom = 100"
            >
              {{ zoom }}%
            </span>
            <el-button size="small" @click="zoom = Math.min(300, zoom + 25)">+</el-button>
          </div>
        </div>

        <div class="image-stage">
          <img
            v-if="currentPreviewUrl"
            :src="currentPreviewUrl"
            :alt="activeQuotation?.source_filename || '原图预览'"
            :style="{ width: `${zoom}%`, maxWidth: 'none', borderRadius: '8px' }"
          />
          <div v-else style="padding: 36px 16px; text-align: center">
            <div style="font-weight: 600; margin-bottom: 6px">已从云端记录 / 草稿恢复表格</div>
            <div class="glass-muted" style="font-size: 12.5px; line-height: 1.6">
              为节约存储空间，云端存档与刷新草稿不留存原始图片文件。<br />
              你可直接继续修改右侧表格并重新导出 .xlsx / .zip。
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧：结构化可编辑表格 + 文本模式预览 -->
      <div style="min-width: 0">
        <div
          v-if="mode === 'text' && activeQuotation?.plain_text"
          class="glass"
          style="padding: 14px; margin-bottom: 14px"
        >
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px">
            <span style="font-weight: 700; font-size: 13.5px">文本 / Markdown 提取结果</span>
            <el-button size="small" @click="copyPlainText">复制纯文本</el-button>
          </div>
          <pre
            style="
              margin: 0;
              white-space: pre-wrap;
              word-break: break-word;
              font-family: var(--font-mono);
              font-size: 12.5px;
              color: var(--glass-text-secondary);
              max-height: 200px;
              overflow: auto;
            "
          >{{ activeQuotation.plain_text }}</pre>
        </div>

        <div v-if="activeSheet" class="spreadsheet-wrap">
          <table class="sheet-table">
            <thead>
              <tr>
                <th class="sheet-row-index">#</th>
                <th v-for="(header, cIdx) in activeSheet.headers" :key="`h-${cIdx}`">
                  <div style="display: flex; flex-direction: column; gap: 5px">
                    <div style="display: flex; align-items: center; gap: 4px">
                      <input
                        :value="header"
                        class="cell-input"
                        style="font-weight: 700"
                        @input="updateHeader(cIdx, ($event.target as HTMLInputElement).value)"
                      />
                      <button
                        v-if="activeSheet.headers.length > 1"
                        type="button"
                        title="删除此列"
                        style="
                          border: none;
                          background: transparent;
                          color: var(--glass-text-muted);
                          cursor: pointer;
                          font-size: 13px;
                        "
                        @click="removeColumn(cIdx)"
                      >
                        ×
                      </button>
                    </div>
                    <el-select
                      :model-value="activeSheet.col_types[cIdx] || 'text'"
                      size="small"
                      @change="(val: ColumnSemanticType) => updateColType(cIdx, val)"
                    >
                      <el-option label="文本 Text" value="text" />
                      <el-option label="数值 Number" value="number" />
                      <el-option label="货币 Currency" value="currency" />
                      <el-option label="百分比 Percent" value="percent" />
                      <el-option label="日期 Date" value="date" />
                    </el-select>
                  </div>
                </th>
                <th class="sheet-row-index">操作</th>
              </tr>
            </thead>
            <tbody v-auto-animate="activeSheet.rows.length <= 200 ? {} : undefined">
              <tr
                v-for="(row, rIdx) in activeSheet.rows"
                :key="`r-${rIdx}`"
                :style="
                  isSectionHeaderRow(rIdx)
                    ? { background: 'rgba(169, 208, 142, 0.28)', fontWeight: '700' }
                    : undefined
                "
              >
                <td class="sheet-row-index num-tabular">{{ rIdx + 1 }}</td>
                <td
                  v-for="(cell, cIdx) in row"
                  :key="`c-${rIdx}-${cIdx}`"
                  :class="{ 'cell-warning': Boolean(getWarning(rIdx, cIdx)) }"
                  :title="
                    isSectionHeaderRow(rIdx)
                      ? '区块小表头行（导出将自动标绿加粗）'
                      : getWarning(rIdx, cIdx) || '点击可直接修改单元格'
                  "
                >
                  <input
                    :value="cell"
                    class="cell-input"
                    :class="{ 'num-tabular': !isSectionHeaderRow(rIdx) && isNumericCol(cIdx) }"
                    :style="{
                      textAlign: isSectionHeaderRow(rIdx) || isNumericCol(cIdx) ? 'center' : 'left',
                      fontWeight: isSectionHeaderRow(rIdx) ? '700' : undefined,
                    }"
                    @input="updateCell(rIdx, cIdx, ($event.target as HTMLInputElement).value)"
                    @blur="handleCellBlur(rIdx, cIdx)"
                  />
                </td>
                <td class="sheet-row-index">
                  <button
                    type="button"
                    title="删除此行"
                    style="
                      border: none;
                      background: transparent;
                      color: var(--glass-text-muted);
                      cursor: pointer;
                      font-size: 13px;
                    "
                    @click="removeRow(rIdx)"
                  >
                    删
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import SpotlightCard from "./SpotlightCard.vue";
import {
  downloadExportedTables,
  extractApiError,
  upsertModelDict,
  type ColumnSemanticType,
  type ExtractedSheet,
  type QuotationGroup,
} from "../api/convertApi";

const props = defineProps<{
  quotations: QuotationGroup[];
  mergePerImage: boolean;
  previewMap: Record<string, string>;
  mode: "excel" | "text";
}>();

const emit = defineEmits<{
  (e: "update:quotations", val: QuotationGroup[]): void;
  (e: "update:mergePerImage", val: boolean): void;
  (e: "clear-draft"): void;
  (e: "dict-updated", total: number): void;
}>();

const activeQuoteIdx = ref(0);
const activeSubSheetIdx = ref(0);
const showPreview = ref(true);
const zoom = ref(100);
const exportingKey = ref<"" | "current_xlsx" | "batch_zip" | "all_in_one_xlsx" | "csv">("");
const importingDict = ref(false);
const dirtyLearnRows = new Set<string>();

watch(
  () => props.quotations.length,
  (len) => {
    if (activeQuoteIdx.value >= len) {
      activeQuoteIdx.value = Math.max(0, len - 1);
      activeSubSheetIdx.value = 0;
    }
  }
);

watch(
  () => props.mergePerImage,
  () => {
    activeSubSheetIdx.value = 0;
  }
);

const activeQuotation = computed(() => props.quotations[activeQuoteIdx.value] || null);

const currentSheets = computed<ExtractedSheet[]>(() => {
  const q = activeQuotation.value;
  if (!q) return [];
  if (props.mergePerImage) return [q.merged_sheet];
  return q.raw_sheets?.length ? q.raw_sheets : [q.merged_sheet];
});

const activeSheet = computed<ExtractedSheet | null>(
  () => currentSheets.value[activeSubSheetIdx.value] || currentSheets.value[0] || null
);

const currentTotalRows = computed(() =>
  currentSheets.value.reduce((acc, sh) => acc + (sh.rows?.length || 0), 0)
);

const currentPreviewUrl = computed(() => {
  const fname = activeQuotation.value?.source_filename;
  if (fname && props.previewMap[fname]) return props.previewMap[fname];
  return "";
});

function selectQuotation(idx: number) {
  activeQuoteIdx.value = idx;
  activeSubSheetIdx.value = 0;
}

function cloneQuotations(): QuotationGroup[] {
  return JSON.parse(JSON.stringify(props.quotations));
}

function getMutableTargetSheet(next: QuotationGroup[]): ExtractedSheet | null {
  const q = next[activeQuoteIdx.value];
  if (!q) return null;
  if (props.mergePerImage) return q.merged_sheet;
  return q.raw_sheets?.[activeSubSheetIdx.value] || q.merged_sheet;
}

function updateQuoteTitle(val: string) {
  const next = cloneQuotations();
  const q = next[activeQuoteIdx.value];
  if (!q) return;
  q.quote_title = val;
  q.merged_sheet.name = val.slice(0, 28) || "Sheet1";
  q.merged_sheet.export_filename = val || q.source_filename;
  emit("update:quotations", next);
}

function isSectionHeaderRow(rIdx: number): boolean {
  return Boolean(activeSheet.value?.section_header_rows?.includes(rIdx));
}

function getWarning(rIdx: number, cIdx: number): string {
  const w = activeSheet.value?.warnings?.find((item) => item.row === rIdx && item.col === cIdx);
  return w ? w.reason : "";
}

function isNumericCol(cIdx: number): boolean {
  const t = activeSheet.value?.col_types?.[cIdx];
  return t === "number" || t === "currency" || t === "percent";
}

function updateHeader(cIdx: number, val: string) {
  const next = cloneQuotations();
  const sh = getMutableTargetSheet(next);
  if (!sh) return;
  sh.headers[cIdx] = val;
  emit("update:quotations", next);
}

function updateColType(cIdx: number, val: ColumnSemanticType) {
  const next = cloneQuotations();
  const sh = getMutableTargetSheet(next);
  if (!sh) return;
  sh.col_types[cIdx] = val;
  emit("update:quotations", next);
}

function detectModelAndCodeCols(headers: string[]): { modelCol: number; codeCol: number } {
  const normed = (headers || []).map((h) => String(h || "").replace(/\s+/g, "").toLowerCase());
  let modelCol = -1;
  let codeCol = -1;
  for (let i = 0; i < normed.length; i++) {
    const h = normed[i];
    if (modelCol === -1 && ["型号", "机型", "model", "设备名称", "手机型号", "平板型号"].includes(h)) {
      modelCol = i;
    }
  }
  for (let i = 0; i < normed.length; i++) {
    if (i === modelCol) continue;
    const h = normed[i];
    if (codeCol === -1 && ["网络型号", "小型号", "型号编码", "机器编码", "代号", "sku", "编码"].some((k) => h.includes(k))) {
      codeCol = i;
    } else if (modelCol === -1 && ["型号", "机型", "model", "系列", "pad", "平板"].some((k) => h.includes(k))) {
      modelCol = i;
    }
  }
  if (codeCol === -1 && modelCol === 0 && normed.length > 1 && normed[1].includes("具体型号")) {
    codeCol = 1;
  }
  return { modelCol, codeCol };
}

const activeModelCodeCols = computed(() => {
  const sh = activeSheet.value;
  if (!sh || sh.style_preset === "general") return { modelCol: -1, codeCol: -1 };
  return detectModelAndCodeCols(sh.headers || []);
});

const hasModelAndCodeCols = computed(
  () => activeModelCodeCols.value.modelCol >= 0 && activeModelCodeCols.value.codeCol >= 0
);

function updateCell(rIdx: number, cIdx: number, val: string) {
  const next = cloneQuotations();
  const sh = getMutableTargetSheet(next);
  if (!sh) return;
  const cols = detectModelAndCodeCols(sh.headers || []);
  const wasWarned = Boolean(sh.warnings?.some((w) => w.row === rIdx && w.col === cIdx));
  if (
    sh.style_preset !== "general" &&
    cols.modelCol >= 0 &&
    cols.codeCol >= 0 &&
    (cIdx === cols.modelCol || cIdx === cols.codeCol)
  ) {
    dirtyLearnRows.add(`${activeQuoteIdx.value}:${activeSubSheetIdx.value}:${rIdx}:${wasWarned ? "1" : "0"}`);
  }
  sh.rows[rIdx][cIdx] = val;
  sh.warnings = sh.warnings.filter((w) => !(w.row === rIdx && w.col === cIdx));
  emit("update:quotations", next);
}

async function handleCellBlur(rIdx: number, cIdx: number) {
  const sh = activeSheet.value;
  if (!sh || sh.style_preset === "general") return;
  const cols = activeModelCodeCols.value;
  if (cols.modelCol < 0 || cols.codeCol < 0 || (cIdx !== cols.modelCol && cIdx !== cols.codeCol)) return;

  const kWarn = `${activeQuoteIdx.value}:${activeSubSheetIdx.value}:${rIdx}:1`;
  const kNorm = `${activeQuoteIdx.value}:${activeSubSheetIdx.value}:${rIdx}:0`;
  const hadDirty = dirtyLearnRows.has(kWarn) || dirtyLearnRows.has(kNorm);
  if (!hadDirty) return;
  dirtyLearnRows.delete(kWarn);
  dirtyLearnRows.delete(kNorm);

  const row = sh.rows[rIdx];
  if (!row) return;
  const modelVal = String(row[cols.modelCol] || "").trim();
  const codeVal = String(row[cols.codeCol] || "").trim();
  if (!modelVal || !codeVal || modelVal.endsWith("系列")) return;

  // 若用户在型号列填入了准确机型，自动顺手填充本表内其余「同网络型号且为空白标黄」的行（如截图 3 的第 59、60 行 ADA-AL10U！）
  let siblingFilled = 0;
  if (cIdx === cols.modelCol) {
    const normCode = codeVal.replace(/[\s\-_/]+/g, "").toLowerCase();
    const next = cloneQuotations();
    const targetSh = getMutableTargetSheet(next);
    if (targetSh) {
      for (let i = 0; i < targetSh.rows.length; i++) {
        if (i === rIdx) continue;
        const r = targetSh.rows[i];
        const rModel = String(r[cols.modelCol] || "").trim();
        const rCode = String(r[cols.codeCol] || "").replace(/[\s\-_/]+/g, "").toLowerCase();
        if (!rModel && rCode && rCode === normCode) {
          r[cols.modelCol] = modelVal;
          targetSh.warnings = targetSh.warnings.filter((w) => !(w.row === i && w.col === cols.modelCol));
          siblingFilled++;
        }
      }
      if (siblingFilled > 0) {
        emit("update:quotations", next);
      }
    }
  }

  // 自动写入云端「机型 ↔ 网络型号」记忆库（零阻塞，静默记住下次直接消歧）
  try {
    const brandHint = (activeQuotation.value?.quote_title || "").split(/\s+/)[0]?.slice(0, 12) || "";
    const res = await upsertModelDict({
      items: [{ model_name: modelVal, network_code: codeVal, brand_hint: brandHint }],
      source: "auto_edit",
    });
    emit("dict-updated", res.total);
    ElMessage.success(
      siblingFilled > 0
        ? `已同步填充同网码 ${siblingFilled} 行并记住「${modelVal} ↔ ${codeVal}」，下次遇歧义自动补全`
        : `已自动记住「${modelVal} ↔ ${codeVal}」，下次遇歧义自动消歧`
    );
  } catch {
    // 静默忽略网络偶发波动，不影响表格编辑
  }
}

function collectCleanPairsFromSheet(sh: ExtractedSheet, brandHint: string): Array<{ model_name: string; network_code: string; brand_hint: string }> {
  if (!sh || sh.style_preset === "general") return [];
  const { modelCol, codeCol } = detectModelAndCodeCols(sh.headers || []);
  if (modelCol < 0 || codeCol < 0) return [];
  const secSet = new Set(sh.section_header_rows || []);
  const warnRows = new Set((sh.warnings || []).filter((w) => w.col === modelCol || w.col === codeCol).map((w) => w.row));
  const seen = new Set<string>();
  const out: Array<{ model_name: string; network_code: string; brand_hint: string }> = [];
  for (let i = 0; i < (sh.rows || []).length; i++) {
    if (secSet.has(i) || warnRows.has(i)) continue;
    const m = String(sh.rows[i][modelCol] || "").trim();
    const c = String(sh.rows[i][codeCol] || "").trim();
    if (!m || !c || m.endsWith("系列") || !/[A-Za-z0-9]/.test(c)) continue;
    const key = `${m.toLowerCase()}||${c.toLowerCase()}`;
    if (!seen.has(key)) {
      seen.add(key);
      out.push({ model_name: m, network_code: c, brand_hint: brandHint });
    }
  }
  return out;
}

async function importCurrentSheetToDict() {
  const sh = activeSheet.value;
  if (!sh || importingDict.value) return;
  const brandHint = (activeQuotation.value?.quote_title || "").split(/\s+/)[0]?.slice(0, 12) || "";
  const pairs = collectCleanPairsFromSheet(sh, brandHint);
  if (pairs.length === 0) {
    ElMessage.warning("当前表没有可收录的「机型 + 网络型号」完整记录");
    return;
  }
  importingDict.value = true;
  try {
    const res = await upsertModelDict({ items: pairs, source: "sheet_import" });
    emit("dict-updated", res.total);
    ElMessage.success(`当前表已收录进云端字典：新增 ${res.created} 条、更新 ${res.updated} 条（共 ${res.total} 条）`);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    importingDict.value = false;
  }
}

async function importAllSheetsToDict() {
  if (importingDict.value || props.quotations.length === 0) return;
  const allPairs: Array<{ model_name: string; network_code: string; brand_hint: string }> = [];
  const seen = new Set<string>();
  for (const q of props.quotations) {
    const brandHint = (q.quote_title || "").split(/\s+/)[0]?.slice(0, 12) || "";
    for (const pair of collectCleanPairsFromSheet(q.merged_sheet, brandHint)) {
      const k = `${pair.model_name.toLowerCase()}||${pair.network_code.toLowerCase()}`;
      if (!seen.has(k)) {
        seen.add(k);
        allPairs.push(pair);
      }
    }
  }
  if (allPairs.length === 0) {
    ElMessage.warning("本批表格暂无可收录的完整机型映射");
    return;
  }
  importingDict.value = true;
  try {
    let totalCreated = 0;
    let totalUpdated = 0;
    let finalTotal = 0;
    // 分批提交（每批最多 500 条）
    for (let i = 0; i < allPairs.length; i += 500) {
      const res = await upsertModelDict({ items: allPairs.slice(i, i + 500), source: "sheet_import" });
      totalCreated += res.created;
      totalUpdated += res.updated;
      finalTotal = res.total;
    }
    emit("dict-updated", finalTotal);
    ElMessage.success(`全部 ${props.quotations.length} 份表已收录进云端字典：新增 ${totalCreated} 条、更新 ${totalUpdated} 条（共 ${finalTotal} 条）`);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    importingDict.value = false;
  }
}

function addRow() {
  const next = cloneQuotations();
  const sh = getMutableTargetSheet(next);
  if (!sh) return;
  sh.rows.push(new Array(sh.headers.length).fill(""));
  emit("update:quotations", next);
}

function removeRow(rIdx: number) {
  const next = cloneQuotations();
  const sh = getMutableTargetSheet(next);
  if (!sh) return;
  sh.rows.splice(rIdx, 1);
  sh.warnings = sh.warnings
    .filter((w) => w.row !== rIdx)
    .map((w) => (w.row > rIdx ? { ...w, row: w.row - 1 } : w));
  if (sh.section_header_rows?.length) {
    sh.section_header_rows = sh.section_header_rows
      .filter((r) => r !== rIdx)
      .map((r) => (r > rIdx ? r - 1 : r));
  }
  emit("update:quotations", next);
}

function addColumn() {
  const next = cloneQuotations();
  const sh = getMutableTargetSheet(next);
  if (!sh) return;
  sh.headers.push(`新增列${sh.headers.length + 1}`);
  sh.col_types.push("text");
  for (const r of sh.rows) r.push("");
  emit("update:quotations", next);
}

function removeColumn(cIdx: number) {
  const next = cloneQuotations();
  const sh = getMutableTargetSheet(next);
  if (!sh || sh.headers.length <= 1) return;
  sh.headers.splice(cIdx, 1);
  sh.col_types.splice(cIdx, 1);
  for (const r of sh.rows) r.splice(cIdx, 1);
  sh.warnings = sh.warnings
    .filter((w) => w.col !== cIdx)
    .map((w) => (w.col > cIdx ? { ...w, col: w.col - 1 } : w));
  emit("update:quotations", next);
}

function buildExportSheetsForQuotation(q: QuotationGroup): ExtractedSheet[] {
  const shouldSplit = (!props.mergePerImage || Boolean(q.suggest_split_sheets)) && (q.raw_sheets?.length || 0) > 1;
  if (!shouldSplit) {
    return [{ ...q.merged_sheet, name: (q.quote_title || q.merged_sheet.name).slice(0, 28), export_filename: q.quote_title }];
  }
  return (q.raw_sheets?.length ? q.raw_sheets : [q.merged_sheet]).map((s) => ({
    ...s,
    export_filename: `${q.quote_title}_${s.name}`,
  }));
}

async function exportCurrentXlsx() {
  const q = activeQuotation.value;
  if (!q) return;
  exportingKey.value = "current_xlsx";
  try {
    const sheets = buildExportSheetsForQuotation(q);
    const dlName = await downloadExportedTables({
      sheets,
      outputMode: "all_in_one",
      format: "xlsx",
      filename: q.quote_title || q.source_filename.replace(/\.[^.]+$/, ""),
    });
    ElMessage.success(`已导出单份报价单：${dlName}`);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    exportingKey.value = "";
  }
}

async function exportAllZip() {
  exportingKey.value = "batch_zip";
  try {
    const sheets = props.quotations.map((q) => ({
      ...q.merged_sheet,
      name: (q.quote_title || q.merged_sheet.name).slice(0, 28),
      export_filename: q.quote_title || q.source_filename.replace(/\.[^.]+$/, ""),
    }));
    const dlName = await downloadExportedTables({
      sheets,
      outputMode: "per_image",
      format: "xlsx",
      filename: `报价单合集_${props.quotations.length}份`,
    });
    ElMessage.success(`已打包导出全部报价单：${dlName}`);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    exportingKey.value = "";
  }
}

async function exportAllInOneXlsx() {
  exportingKey.value = "all_in_one_xlsx";
  try {
    const sheets = props.quotations.flatMap((q) => buildExportSheetsForQuotation(q));
    const dlName = await downloadExportedTables({
      sheets,
      outputMode: "all_in_one",
      format: "xlsx",
      filename: `报价单合并总表_${props.quotations.length}份`,
    });
    ElMessage.success(`已导出合并多 Sheet 工作簿：${dlName}`);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    exportingKey.value = "";
  }
}

async function exportCsv() {
  const q = activeQuotation.value;
  if (!q) return;
  exportingKey.value = "csv";
  try {
    const sheets = buildExportSheetsForQuotation(q);
    const dlName = await downloadExportedTables({
      sheets,
      outputMode: "per_image",
      format: "csv",
      filename: q.quote_title || "报价单",
    });
    ElMessage.success(`已导出：${dlName}`);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    exportingKey.value = "";
  }
}

async function copyMarkdown() {
  const sh = activeSheet.value;
  if (!sh) return;
  const headerLine = `| ${sh.headers.join(" | ")} |`;
  const sepLine = `| ${sh.headers.map(() => "---").join(" | ")} |`;
  const bodyLines = sh.rows.map((r) => `| ${r.join(" | ")} |`);
  await navigator.clipboard.writeText([headerLine, sepLine, ...bodyLines].join("\n"));
  ElMessage.success("当前报价单 Markdown 已复制到剪贴板");
}

async function copyPlainText() {
  await navigator.clipboard.writeText(activeQuotation.value?.plain_text || "");
  ElMessage.success("纯文本结果已复制到剪贴板");
}
</script>
