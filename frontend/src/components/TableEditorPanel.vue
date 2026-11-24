<template>
  <section class="glass-strong" style="padding: 20px; margin-bottom: 24px">
    <!-- 顶部公告与导出栏 -->
    <div
      style="
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 16px;
      "
    >
      <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap">
        <span class="gem-pill" :class="isDemo ? 'pill-gold' : 'pill-emerald'">
          {{ isDemo ? "演示提取模式" : `AI 引擎：${engine}` }}
        </span>
        <span v-if="notice" class="glass-muted" style="font-size: 13px">{{ notice }}</span>
      </div>

      <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
        <el-radio-group v-model="localOutputMode" size="small">
          <el-radio-button value="all_in_one">合并为单文件 (多 Sheet)</el-radio-button>
          <el-radio-button value="per_image">每表独立导出</el-radio-button>
        </el-radio-group>
        <el-button size="small" @click="copyMarkdown">复制 Markdown</el-button>
        <el-button size="small" :loading="exportingFormat === 'csv'" @click="triggerExport('csv')">
          导出 .csv
        </el-button>
        <el-button
          type="primary"
          size="small"
          :loading="exportingFormat === 'xlsx'"
          @click="triggerExport('xlsx')"
        >
          导出 .xlsx
        </el-button>
      </div>
    </div>

    <!-- Bento KPI 概览 -->
    <div class="bento-grid">
      <SpotlightCard>
        <div class="kpi-label">提取工作表</div>
        <div class="kpi-value num-tabular">{{ sheets.length }}</div>
        <span class="gem-pill pill-sky">可多表合并</span>
      </SpotlightCard>
      <SpotlightCard>
        <div class="kpi-label">总数据行数</div>
        <div class="kpi-value num-tabular">{{ totalRows }}</div>
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

    <!-- Sheet 标签页与编辑工具栏 -->
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
        <button
          v-for="(sh, idx) in sheets"
          :key="`${idx}-${sh.name}`"
          type="button"
          class="nav-pill-btn"
          :class="{ 'is-active': activeSheetIndex === idx }"
          @click="activeSheetIndex = idx"
        >
          {{ sh.name || `Sheet${idx + 1}` }}
        </button>
      </div>

      <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
        <el-button size="small" @click="showPreview = !showPreview">
          {{ showPreview ? "隐藏原图对照" : "显示原图对照" }}
        </el-button>
        <el-button size="small" @click="addRow">+ 新增行</el-button>
        <el-button size="small" @click="addColumn">+ 新增列</el-button>
        <el-button size="small" type="danger" plain @click="$emit('clear-draft')">关闭工作台</el-button>
      </div>
    </div>

    <!-- 分栏对照主区域：左原图 + 右可编辑表格 -->
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
          <span style="font-weight: 700; font-size: 13.5px; color: var(--glass-text)">
            原图对照：{{ activeSheet?.source_filename || "当前表格" }}
          </span>
          <div style="display: flex; align-items: center; gap: 4px">
            <el-button size="small" @click="zoom = Math.max(50, zoom - 25)">-</el-button>
            <span class="num-tabular" style="font-size: 12px; min-width: 44px; text-align: center">
              {{ zoom }}%
            </span>
            <el-button size="small" @click="zoom = Math.min(200, zoom + 25)">+</el-button>
          </div>
        </div>

        <div class="image-stage">
          <img
            v-if="currentPreviewUrl"
            :src="currentPreviewUrl"
            :alt="activeSheet?.source_filename || '原图预览'"
            :style="{ width: `${zoom}%`, maxWidth: 'none', borderRadius: '8px' }"
          />
          <div v-else style="padding: 36px 16px; text-align: center">
            <div style="font-weight: 600; margin-bottom: 6px">已从本地草稿恢复表格编辑状态</div>
            <div class="glass-muted" style="font-size: 12.5px; line-height: 1.6">
              为保护隐私与内存，刷新后浏览器不会持久化原始图片文件流。<br />
              你仍可直接编辑右侧表格并导出 .xlsx / .csv，或重新拖入原图对照。
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧：结构化可编辑表格 + 文本模式预览 -->
      <div style="min-width: 0">
        <div v-if="mode === 'text' && plainText" class="glass" style="padding: 14px; margin-bottom: 14px">
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
          >{{ plainText }}</pre>
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
              <tr v-for="(row, rIdx) in activeSheet.rows" :key="`r-${rIdx}`">
                <td class="sheet-row-index num-tabular">{{ rIdx + 1 }}</td>
                <td
                  v-for="(cell, cIdx) in row"
                  :key="`c-${rIdx}-${cIdx}`"
                  :class="{ 'cell-warning': Boolean(getWarning(rIdx, cIdx)) }"
                  :title="getWarning(rIdx, cIdx) || '点击可直接修改单元格'"
                >
                  <input
                    :value="cell"
                    class="cell-input"
                    :class="{ 'num-tabular': isNumericCol(cIdx) }"
                    :style="{ textAlign: isNumericCol(cIdx) ? 'right' : 'left' }"
                    @input="updateCell(rIdx, cIdx, ($event.target as HTMLInputElement).value)"
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
  type ColumnSemanticType,
  type ExtractedSheet,
} from "../api/convertApi";

const props = defineProps<{
  sheets: ExtractedSheet[];
  previewMap: Record<string, string>;
  outputMode: "all_in_one" | "per_image";
  mode: "excel" | "text";
  engine: string;
  isDemo: boolean;
  notice: string;
  plainText: string;
}>();

const emit = defineEmits<{
  (e: "update:sheets", val: ExtractedSheet[]): void;
  (e: "update:outputMode", val: "all_in_one" | "per_image"): void;
  (e: "clear-draft"): void;
}>();

const activeSheetIndex = ref(0);
const showPreview = ref(true);
const zoom = ref(100);
const exportingFormat = ref<"" | "xlsx" | "csv">("");

const localOutputMode = computed({
  get: () => props.outputMode,
  set: (val) => emit("update:outputMode", val),
});

watch(
  () => props.sheets.length,
  (len) => {
    if (activeSheetIndex.value >= len) {
      activeSheetIndex.value = Math.max(0, len - 1);
    }
  }
);

const activeSheet = computed(() => props.sheets[activeSheetIndex.value] || null);
const totalRows = computed(() => props.sheets.reduce((acc, s) => acc + (s.rows?.length || 0), 0));

const currentPreviewUrl = computed(() => {
  const fname = activeSheet.value?.source_filename;
  if (fname && props.previewMap[fname]) return props.previewMap[fname];
  const firstKey = Object.keys(props.previewMap)[0];
  return firstKey ? props.previewMap[firstKey] : "";
});

function cloneSheets(): ExtractedSheet[] {
  return JSON.parse(JSON.stringify(props.sheets));
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
  const next = cloneSheets();
  next[activeSheetIndex.value].headers[cIdx] = val;
  emit("update:sheets", next);
}

function updateColType(cIdx: number, val: ColumnSemanticType) {
  const next = cloneSheets();
  next[activeSheetIndex.value].col_types[cIdx] = val;
  emit("update:sheets", next);
}

function updateCell(rIdx: number, cIdx: number, val: string) {
  const next = cloneSheets();
  const sheet = next[activeSheetIndex.value];
  sheet.rows[rIdx][cIdx] = val;
  sheet.warnings = sheet.warnings.filter((w) => !(w.row === rIdx && w.col === cIdx));
  emit("update:sheets", next);
}

function addRow() {
  const next = cloneSheets();
  const sheet = next[activeSheetIndex.value];
  if (!sheet) return;
  sheet.rows.push(new Array(sheet.headers.length).fill(""));
  emit("update:sheets", next);
}

function removeRow(rIdx: number) {
  const next = cloneSheets();
  const sheet = next[activeSheetIndex.value];
  if (!sheet) return;
  sheet.rows.splice(rIdx, 1);
  sheet.warnings = sheet.warnings
    .filter((w) => w.row !== rIdx)
    .map((w) => (w.row > rIdx ? { ...w, row: w.row - 1 } : w));
  emit("update:sheets", next);
}

function addColumn() {
  const next = cloneSheets();
  const sheet = next[activeSheetIndex.value];
  if (!sheet) return;
  sheet.headers.push(`新增列${sheet.headers.length + 1}`);
  sheet.col_types.push("text");
  for (const r of sheet.rows) r.push("");
  emit("update:sheets", next);
}

function removeColumn(cIdx: number) {
  const next = cloneSheets();
  const sheet = next[activeSheetIndex.value];
  if (!sheet || sheet.headers.length <= 1) return;
  sheet.headers.splice(cIdx, 1);
  sheet.col_types.splice(cIdx, 1);
  for (const r of sheet.rows) r.splice(cIdx, 1);
  sheet.warnings = sheet.warnings
    .filter((w) => w.col !== cIdx)
    .map((w) => (w.col > cIdx ? { ...w, col: w.col - 1 } : w));
  emit("update:sheets", next);
}

async function triggerExport(format: "xlsx" | "csv") {
  exportingFormat.value = format;
  try {
    const baseName = activeSheet.value?.name || "image-to-excel";
    const dlName = await downloadExportedTables({
      sheets: props.sheets,
      outputMode: props.outputMode,
      format,
      filename: baseName,
    });
    ElMessage.success(`已导出：${dlName}`);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    exportingFormat.value = "";
  }
}

async function copyMarkdown() {
  const sh = activeSheet.value;
  if (!sh) return;
  const headerLine = `| ${sh.headers.join(" | ")} |`;
  const sepLine = `| ${sh.headers.map(() => "---").join(" | ")} |`;
  const bodyLines = sh.rows.map((r) => `| ${r.join(" | ")} |`);
  await navigator.clipboard.writeText([headerLine, sepLine, ...bodyLines].join("\n"));
  ElMessage.success("当前工作表 Markdown 已复制到剪贴板");
}

async function copyPlainText() {
  await navigator.clipboard.writeText(props.plainText || "");
  ElMessage.success("纯文本结果已复制到剪贴板");
}
</script>
