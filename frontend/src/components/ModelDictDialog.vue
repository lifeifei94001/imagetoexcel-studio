<template>
  <el-dialog
    :model-value="visible"
    title="云端机型 ↔ 网络型号记忆字典（分品牌隔离）"
    width="860px"
    max-width="95vw"
    align-center
    destroy-on-close
    @update:model-value="(v: boolean) => emit('update:visible', v)"
  >
    <div style="display: flex; flex-direction: column; gap: 14px">
      <div
        class="glass"
        style="
          padding: 10px 14px;
          border-radius: var(--glass-radius-sm);
          font-size: 12.5px;
          line-height: 1.6;
          color: var(--glass-text-secondary);
        "
      >
        <strong>零 AI Token · 分品牌严格隔离消歧：</strong>系统会自动根据报价单标题/表头/文件名识别标准品牌（苹果、华为、荣耀、小米等），仅在同品牌下且双模型出现分歧或留空标黄时自动补全，<strong>绝不会把小米 14 Pro 误填进苹果表</strong>；工作台修改黄格后也会自动记住。
      </div>

      <!-- 快速单条新增 -->
      <div style="display: flex; gap: 8px; flex-wrap: wrap; align-items: center">
        <el-input
          v-model="newModelName"
          size="small"
          placeholder="机型名称，如 Nova 12 Ultra / 14 Pro"
          style="flex: 1.2; min-width: 175px"
          @keyup.enter="handleAddSingle"
        />
        <el-input
          v-model="newNetworkCode"
          size="small"
          placeholder="网络型号（整格原样），如 ADA-AL10U / A2892"
          style="flex: 1.35; min-width: 205px"
          @keyup.enter="handleAddSingle"
        />
        <el-select
          v-model="newBrandHint"
          size="small"
          filterable
          allow-create
          clearable
          default-first-option
          placeholder="所属品牌"
          style="width: 120px"
        >
          <el-option v-for="b in COMMON_BRANDS" :key="b" :label="b" :value="b" />
        </el-select>
        <el-button size="small" type="primary" :loading="saving" @click="handleAddSingle">
          添加词条
        </el-button>
      </div>

      <!-- 搜索 + 批量粘贴导入（支持直接从 WPS / Excel 选中 A/B 两列复制粘贴并统一指定品牌） -->
      <div>
        <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap">
          <div style="display: flex; align-items: center; gap: 8px; flex: 1; min-width: 220px">
            <el-input
              v-model="searchQuery"
              size="small"
              clearable
              placeholder="搜索机型名称、网络型号或品牌..."
              style="max-width: 260px"
              @input="debouncedLoad"
            />
            <span class="gem-pill pill-sky num-tabular">共 {{ totalCount }} 条</span>
          </div>
          <div style="display: flex; gap: 8px; flex-wrap: wrap">
            <el-button size="small" plain @click="showBulkBox = !showBulkBox">
              {{ showBulkBox ? "收起批量导入" : "从 Excel 批量粘贴导入" }}
            </el-button>
            <el-button size="small" :loading="loading" @click="loadList">刷新</el-button>
            <el-button
              size="small"
              type="danger"
              plain
              :disabled="totalCount === 0"
              @click="handleClearAll"
            >
              清空字典
            </el-button>
          </div>
        </div>

        <!-- 品牌快速筛选条 -->
        <div
          v-if="brandSummaries.length > 0"
          style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 10px"
        >
          <span class="glass-muted" style="font-size: 12px; font-weight: 600">按品牌筛选：</span>
          <button
            type="button"
            class="nav-pill-btn"
            style="padding: 3px 10px; font-size: 12px"
            :class="{ 'is-active': selectedBrand === '' }"
            @click="selectBrandFilter('')"
          >
            全部 ({{ totalCount }})
          </button>
          <button
            v-for="bs in brandSummaries"
            :key="bs.brand"
            type="button"
            class="nav-pill-btn"
            style="padding: 3px 10px; font-size: 12px"
            :class="{ 'is-active': selectedBrand === (bs.brand === '未分类' ? '__untagged__' : bs.brand) }"
            @click="selectBrandFilter(bs.brand === '未分类' ? '__untagged__' : bs.brand)"
          >
            {{ bs.brand }} ({{ bs.count }})
          </button>
        </div>

        <div
          v-if="showBulkBox"
          class="glass"
          style="margin-top: 10px; padding: 12px; border-radius: var(--glass-radius-sm); display: flex; flex-direction: column; gap: 8px"
        >
          <div style="display: flex; justify-content: space-between; align-items: center; gap: 8px; flex-wrap: wrap">
            <span class="glass-muted" style="font-size: 12px">
              直接从 WPS / Excel 选中「型号」与「网络型号」两列（或带品牌三列）复制粘贴（支持 Tab、<code>|</code> 或逗号分隔）：
            </span>
            <div style="display: flex; align-items: center; gap: 6px">
              <span style="font-size: 12px; font-weight: 600">默认品牌：</span>
              <el-select
                v-model="bulkDefaultBrand"
                size="small"
                filterable
                allow-create
                clearable
                default-first-option
                placeholder="自动识别(推荐选品牌)"
                style="width: 155px"
              >
                <el-option v-for="b in COMMON_BRANDS" :key="b" :label="b" :value="b" />
              </el-select>
            </div>
          </div>
          <el-input
            v-model="bulkText"
            type="textarea"
            :rows="5"
            placeholder="示例（两列时自动套用右上角默认品牌，也可写三列）：&#10;P50 E 4G	ABR-AL60 ABR-AL90	华为&#10;Nova 12 Ultra	ADA-AL10U	华为&#10;14 Pro	A2892	苹果"
          />
          <div style="display: flex; justify-content: flex-end; gap: 8px">
            <el-button size="small" @click="bulkText = ''">清空输入框</el-button>
            <el-button size="small" type="primary" :loading="saving" @click="handleBulkImport">
              确认批量导入
            </el-button>
          </div>
        </div>
      </div>

      <!-- 字典列表 -->
      <el-table
        v-loading="loading"
        :data="items"
        stripe
        max-height="360"
        style="width: 100%"
        empty-text="暂无机型字典词条。你可在工作台修改黄格自动记住，或点上方「从 Excel 批量粘贴导入」快速建库。"
      >
        <el-table-column prop="model_name" label="机型名称" min-width="170" show-overflow-tooltip />
        <el-table-column prop="network_code" label="网络型号（整格）" min-width="180" show-overflow-tooltip />
        <el-table-column label="所属品牌" width="115" show-overflow-tooltip>
          <template #default="{ row }">
            <span
              class="gem-pill"
              :class="(row.canonical_brand || row.brand_hint) ? 'pill-violet' : 'pill-gold'"
              :title="row.brand_hint && row.canonical_brand && row.brand_hint !== row.canonical_brand ? `原备注：${row.brand_hint}` : undefined"
            >
              {{ row.canonical_brand || row.brand_hint || "未分类" }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="自动消歧命中" width="108" align="center">
          <template #default="{ row }">
            <span class="gem-pill num-tabular" :class="row.hit_count > 0 ? 'pill-emerald' : 'pill-sky'">
              {{ row.hit_count || 0 }} 次
            </span>
          </template>
        </el-table-column>
        <el-table-column label="来源" width="88" align="center">
          <template #default="{ row }">
            <span class="glass-muted" style="font-size: 12px">{{ formatSource(row.source) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="72" align="center">
          <template #default="{ row }">
            <el-button size="small" type="danger" text @click="handleDelete(row.id)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  clearModelDict,
  deleteModelDictItem,
  extractApiError,
  fetchModelDict,
  upsertModelDict,
  type ModelCodeMappingItem,
  type ModelDictBrandSummary,
} from "../api/convertApi";

const COMMON_BRANDS = [
  "华为",
  "荣耀",
  "苹果",
  "小米",
  "红米",
  "OPPO",
  "VIVO",
  "iQOO",
  "一加",
  "真我/Realme",
  "三星",
  "华硕/ROG",
  "红魔",
  "努比亚",
  "中兴",
  "联想",
  "摩托罗拉",
  "魅族",
];

const props = defineProps<{
  visible: boolean;
}>();

const emit = defineEmits<{
  (e: "update:visible", val: boolean): void;
  (e: "count-changed", total: number): void;
}>();

const loading = ref(false);
const saving = ref(false);
const items = ref<ModelCodeMappingItem[]>([]);
const brandSummaries = ref<ModelDictBrandSummary[]>([]);
const totalCount = ref(0);
const searchQuery = ref("");
const selectedBrand = ref("");

const newModelName = ref("");
const newNetworkCode = ref("");
const newBrandHint = ref("");
const showBulkBox = ref(false);
const bulkText = ref("");
const bulkDefaultBrand = ref("");

let searchTimer: number | null = null;

watch(
  () => props.visible,
  (open) => {
    if (open) {
      loadList();
    }
  }
);

function formatSource(src: string): string {
  if (src === "auto_edit") return "修格记住";
  if (src === "sheet_import") return "整表收录";
  return "手动录入";
}

function selectBrandFilter(brandKey: string) {
  selectedBrand.value = brandKey;
  loadList();
}

function debouncedLoad() {
  if (searchTimer !== null) window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => {
    loadList();
  }, 220);
}

async function loadList() {
  loading.value = true;
  try {
    const res = await fetchModelDict(searchQuery.value, selectedBrand.value);
    items.value = res.items || [];
    brandSummaries.value = res.brands || [];
    totalCount.value = res.total || 0;
    emit("count-changed", totalCount.value);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    loading.value = false;
  }
}

async function handleAddSingle() {
  const m = newModelName.value.trim();
  const c = newNetworkCode.value.trim();
  if (!m || !c) {
    ElMessage.warning("请同时填写「机型名称」和「网络型号」");
    return;
  }
  saving.value = true;
  try {
    const res = await upsertModelDict({
      items: [{ model_name: m, network_code: c, brand_hint: newBrandHint.value.trim() }],
      source: "manual",
    });
    newModelName.value = "";
    newNetworkCode.value = "";
    totalCount.value = res.total;
    emit("count-changed", res.total);
    ElMessage.success(res.created > 0 ? "已添加到云端机型字典" : "已更新该机型映射");
    await loadList();
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    saving.value = false;
  }
}

function parseBulkLines(
  raw: string,
  defaultBrand: string
): Array<{ model_name: string; network_code: string; brand_hint?: string }> {
  const out: Array<{ model_name: string; network_code: string; brand_hint?: string }> = [];
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    let parts: string[] = [];
    if (trimmed.includes("\t")) {
      parts = trimmed.split("\t").map((s) => s.trim());
    } else if (trimmed.includes("|")) {
      parts = trimmed.split("|").map((s) => s.trim());
    } else if (trimmed.includes(",") || trimmed.includes("，")) {
      parts = trimmed.split(/[,，]/).map((s) => s.trim());
    } else {
      parts = trimmed.split(/\s{2,}/).map((s) => s.trim());
    }
    if (parts.length >= 2 && parts[0] && parts[1]) {
      if (["型号", "机型", "机型名称"].includes(parts[0]) && parts[1].includes("型号")) {
        continue;
      }
      out.push({
        model_name: parts[0],
        network_code: parts[1],
        brand_hint: parts[2] || defaultBrand || "",
      });
    }
  }
  return out;
}

async function handleBulkImport() {
  const parsed = parseBulkLines(bulkText.value, bulkDefaultBrand.value.trim());
  if (parsed.length === 0) {
    ElMessage.warning("未解析到有效行，请确保每行包含「机型名称」和「网络型号」两列（可用 Tab、| 或逗号分隔）");
    return;
  }
  saving.value = true;
  try {
    const res = await upsertModelDict({ items: parsed, source: "manual" });
    totalCount.value = res.total;
    emit("count-changed", res.total);
    bulkText.value = "";
    showBulkBox.value = false;
    ElMessage.success(`批量导入完成：新增 ${res.created} 条、更新 ${res.updated} 条（当前共 ${res.total} 条）`);
    await loadList();
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    saving.value = false;
  }
}

async function handleDelete(id: number) {
  try {
    const res = await deleteModelDictItem(id);
    items.value = items.value.filter((it) => it.id !== id);
    totalCount.value = res.total;
    emit("count-changed", res.total);
    ElMessage.success("已删除词条");
    await loadList();
  } catch (err) {
    ElMessage.error(extractApiError(err));
  }
}

async function handleClearAll() {
  try {
    await ElMessageBox.confirm("确认清空云端全部「机型 ↔ 网络型号」映射词条？（不可恢复）", "清空机型字典", {
      confirmButtonText: "确认清空",
      cancelButtonText: "取消",
      type: "warning",
    });
  } catch {
    return;
  }
  try {
    await clearModelDict();
    items.value = [];
    brandSummaries.value = [];
    selectedBrand.value = "";
    totalCount.value = 0;
    emit("count-changed", 0);
    ElMessage.success("机型字典已清空");
  } catch (err) {
    ElMessage.error(extractApiError(err));
  }
}
</script>
