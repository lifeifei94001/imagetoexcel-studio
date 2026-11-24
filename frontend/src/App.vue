<template>
  <div class="app-shell">
    <!-- 顶部毛玻璃导航栏 -->
    <header class="glass-strong top-nav">
      <div class="brand-mark" @click="activeTab = 'workspace'">
        <span class="brand-icon">
          <el-icon :size="18"><Grid /></el-icon>
        </span>
        <span>Image to Excel Studio</span>
      </div>

      <nav class="nav-pills" aria-label="主导航">
        <button
          type="button"
          class="nav-pill-btn"
          :class="{ 'is-active': activeTab === 'workspace' }"
          @click="activeTab = 'workspace'"
        >
          工作台
        </button>
        <button
          type="button"
          class="nav-pill-btn"
          :class="{ 'is-active': activeTab === 'guide' }"
          @click="activeTab = 'guide'"
        >
          使用指南
        </button>
        <button
          type="button"
          class="nav-pill-btn"
          :class="{ 'is-active': activeTab === 'history' }"
          @click="openHistoryTab"
        >
          转换记录
        </button>
        <button
          type="button"
          class="nav-pill-btn"
          :class="{ 'is-active': activeTab === 'faq' }"
          @click="activeTab = 'faq'"
        >
          FAQ
        </button>
      </nav>

      <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
        <span class="gem-pill" :class="geminiConfigured ? 'pill-emerald' : 'pill-gold'">
          {{ geminiConfigured ? `AI 就绪 · ${activeModel}` : "免 Key 演示就绪" }}
        </span>

        <el-select v-model="theme" size="small" style="width: 122px" aria-label="切换主题">
          <el-option label="🌸 霞光毛玻璃" value="aurora" />
          <el-option label="🌌 星云毛玻璃" value="nebula" />
          <el-option label="🏛️ 先锋杂志风" value="editorial" />
        </el-select>

        <el-button size="small" @click="glass = !glass">
          {{ glass ? "毛玻璃: 开" : "毛玻璃: 关" }}
        </el-button>

        <el-button size="small" @click="paletteVisible = true">⌘K</el-button>
      </div>
    </header>

    <main class="main-container">
      <!-- ==================== 工作台主视图 ==================== -->
      <template v-if="activeTab === 'workspace'">
        <section class="hero-section">
          <h1 class="hero-title">将表格图片一键转为可编辑 Excel 表格</h1>
          <p class="hero-subtitle">
            上传手机拍照、网页截图或扫描件表格。AI 自动识别行列结构与数值格式，提供原图分栏校对编辑，并一键导出原汁原味
            <code>.xlsx</code> / <code>.csv</code>。
          </p>

          <div class="mode-switch-wrap" role="tablist" aria-label="转换模式">
            <button
              type="button"
              class="mode-tab"
              :class="{ 'is-active': mode === 'excel' }"
              @click="mode = 'excel'"
            >
              Image to Excel
            </button>
            <button
              type="button"
              class="mode-tab"
              :class="{ 'is-active': mode === 'text' }"
              @click="mode = 'text'"
            >
              Image to Text
            </button>
          </div>
        </section>

        <!-- 双栏队列式上传工作台（对齐 imagetoexcel.app 交互体验） -->
        <section class="glass-strong uploader-shell">
          <div class="uploader-grid">
            <!-- 左栏：拖拽 / 粘贴 / 浏览 / 示例试用 -->
            <div
              class="dropzone-box"
              :class="{ 'is-dragover': isDragging }"
              tabindex="0"
              role="button"
              aria-label="拖拽、粘贴或点击选择表格图片"
              @click="fileInputRef?.click()"
              @keydown.enter.prevent="fileInputRef?.click()"
              @dragover.prevent="isDragging = true"
              @dragleave.prevent="isDragging = false"
              @drop.prevent="onDropFiles"
            >
              <input
                ref="fileInputRef"
                type="file"
                multiple
                accept="image/*,.heic,.heif,.jfif"
                style="display: none"
                @change="onFileSelect"
              />
              <el-icon :size="44" style="color: var(--glass-accent)">
                <UploadFilled />
              </el-icon>
              <div style="font-weight: 700; font-size: 16px; color: var(--glass-text)">
                拖拽图片到此处、点击浏览，或直接 <kbd>Ctrl+V</kbd> 粘贴截图
              </div>
              <div class="glass-muted" style="font-size: 13px">
                支持格式：JPG, PNG, GIF, WEBP, JFIF, HEIC · 单张上限 10MB（单批最多 10 张）
              </div>
              <div style="display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; margin-top: 6px" @click.stop>
                <el-button type="primary" @click="fileInputRef?.click()">选择图片文件</el-button>
                <el-button @click="loadSampleImage('sample-inventory-table.png')">
                  试玩：库存盘点表
                </el-button>
                <el-button @click="loadSampleImage('sample-finance-report.png')">
                  试玩：财务营收表
                </el-button>
              </div>
            </div>

            <!-- 右栏：待转换队列 + 输出模式 + 额度提示 + 转换按钮 -->
            <div class="queue-panel">
              <div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px">
                  <span style="font-weight: 700; font-size: 14px">
                    待处理队列 (<span class="num-tabular">{{ queue.length }}</span>/10)
                  </span>
                  <el-checkbox v-if="!geminiConfigured" v-model="forceDemo" size="small" disabled>
                    演示提取模式
                  </el-checkbox>
                  <el-checkbox v-else v-model="forceDemo" size="small">
                    强制用演示模式试玩
                  </el-checkbox>
                </div>

                <div
                  v-if="queue.length === 0"
                  class="glass-muted"
                  style="
                    padding: 28px 12px;
                    text-align: center;
                    font-size: 13px;
                    border: 1px dashed var(--glass-border);
                    border-radius: var(--glass-radius-sm);
                  "
                >
                  暂无图片。点击左侧「试玩示例」或粘贴一张表格截图即可体验。
                </div>

                <div
                  v-else
                  v-auto-animate
                  style="display: flex; flex-direction: column; gap: 8px; max-height: 205px; overflow: auto"
                >
                  <div v-for="(item, idx) in queue" :key="item.id" class="queue-item">
                    <div style="display: flex; align-items: center; gap: 10px; min-width: 0">
                      <img :src="item.previewUrl" :alt="item.file.name" class="queue-thumb" />
                      <div style="min-width: 0">
                        <div
                          style="
                            font-weight: 600;
                            font-size: 13px;
                            white-space: nowrap;
                            overflow: hidden;
                            text-overflow: ellipsis;
                          "
                        >
                          {{ item.file.name }}
                        </div>
                        <div class="glass-muted num-tabular" style="font-size: 12px">
                          {{ formatSize(item.file.size) }}
                        </div>
                      </div>
                    </div>

                    <div style="display: flex; align-items: center; gap: 4px; flex-shrink: 0">
                      <el-button v-if="idx > 0" size="small" text @click="moveQueueItem(idx, -1)">↑</el-button>
                      <el-button
                        v-if="idx < queue.length - 1"
                        size="small"
                        text
                        @click="moveQueueItem(idx, 1)"
                      >
                        ↓
                      </el-button>
                      <el-button size="small" text type="danger" @click="removeQueueItem(idx)">×</el-button>
                    </div>
                  </div>
                </div>
              </div>

              <div style="display: flex; flex-direction: column; gap: 10px; margin-top: 10px">
                <el-input
                  v-model="customHint"
                  size="small"
                  clearable
                  placeholder="可选识别提示（如：保留第 1 列前导零、合并表头按汇总处理）"
                />

                <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px">
                  <span style="font-size: 12.5px; font-weight: 600">导出格式预设：</span>
                  <el-radio-group v-model="outputMode" size="small">
                    <el-radio value="all_in_one">合并进同一 Excel</el-radio>
                    <el-radio value="per_image">一图一表</el-radio>
                  </el-radio-group>
                </div>

                <div
                  class="gem-pill pill-sky"
                  style="justify-content: space-between; padding: 7px 12px; border-radius: var(--glass-radius-sm)"
                >
                  <span>今日剩余免费额度</span>
                  <span class="num-tabular">{{ quotaRemaining }} / {{ quotaLimit }} 次</span>
                </div>

                <div style="display: flex; gap: 10px">
                  <el-button :disabled="queue.length === 0 || extracting" @click="clearQueue">
                    清空队列
                  </el-button>
                  <el-button
                    type="primary"
                    style="flex: 1"
                    :loading="extracting"
                    :disabled="queue.length === 0"
                    @click="startExtraction"
                  >
                    {{ extracting ? "AI 正在解析表格结构..." : "开始提取可编辑表格" }}
                  </el-button>
                </div>
              </div>
            </div>
          </div>

          <div class="glass-muted" style="text-align: center; font-size: 12.5px; margin-top: 12px">
            * 隐私保护声明：上传图片仅在本次内存流中解析，不持久化至数据库，刷新后自动释放。
          </div>
        </section>

        <!-- 分栏交互式编辑器（识别后自动出现并支持 F5 草稿恢复） -->
        <div ref="editorAnchorRef">
          <TableEditorPanel
            v-if="extractedResult"
            v-model:sheets="extractedResult.sheets"
            v-model:output-mode="outputMode"
            :preview-map="previewMap"
            :mode="mode"
            :engine="extractedResult.engine"
            :is-demo="extractedResult.is_demo"
            :notice="extractedResult.notice"
            :plain-text="extractedResult.plain_text"
            @clear-draft="clearDraft"
          />
        </div>

        <!-- 高频场景 Bento 卡片（对应原站 6 大应用场景） -->
        <section style="margin-top: 32px">
          <h2 class="section-heading">支持哪些类型的表格图片？</h2>
          <p class="section-sub">无论是手机斜拍纸质单据，还是网页/PDF 局部截图，都能还原真实行列结构。</p>
          <div class="bento-grid">
            <SpotlightCard v-for="card in useCases" :key="card.title">
              <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px">
                <span style="font-weight: 700; font-size: 15px">{{ card.title }}</span>
                <span class="gem-pill" :class="card.pillClass">{{ card.tag }}</span>
              </div>
              <p class="glass-muted" style="margin: 0; font-size: 13.5px; line-height: 1.6">
                {{ card.desc }}
              </p>
            </SpotlightCard>
          </div>
        </section>
      </template>

      <!-- ==================== 使用指南视图 ==================== -->
      <section v-else-if="activeTab === 'guide'" class="glass-strong" style="padding: 24px">
        <h2 class="section-heading">4 步把表格图片变成干净的 Excel</h2>
        <p class="section-sub">告别手动重录，从传图到导出全程可在浏览器内完成。</p>
        <div class="bento-grid">
          <SpotlightCard v-for="(step, idx) in guideSteps" :key="step.title">
            <span class="gem-pill pill-violet" style="margin-bottom: 10px">STEP 0{{ idx + 1 }}</span>
            <div style="font-weight: 700; font-size: 15.5px; margin-bottom: 6px">{{ step.title }}</div>
            <p class="glass-muted" style="margin: 0; font-size: 13.5px; line-height: 1.65">{{ step.desc }}</p>
          </SpotlightCard>
        </div>
        <div style="margin-top: 12px">
          <el-button type="primary" @click="activeTab = 'workspace'">返回工作台立即转换</el-button>
        </div>
      </section>

      <!-- ==================== 转换记录视图 ==================== -->
      <section v-else-if="activeTab === 'history'" class="glass-strong" style="padding: 22px">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px">
          <div>
            <h2 class="section-heading">最近转换记录（仅元数据摘要）</h2>
            <p class="section-sub" style="margin-bottom: 0">
              严守 5GB 数据库配额红线：仅记录文件名、行列规模与耗时状态，绝不保存原图二进制。
            </p>
          </div>
          <el-button size="small" :loading="loadingHistory" @click="loadHistory">刷新记录</el-button>
        </div>

        <el-table :data="historyList" stripe style="width: 100%" empty-text="暂无转换记录">
          <el-table-column prop="id" label="ID" width="70" />
          <el-table-column prop="filename" label="源图片文件名" min-width="200" />
          <el-table-column label="文件大小" width="110">
            <template #default="{ row }">
              <span class="num-tabular">{{ formatSize(row.file_size_bytes) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="提取规模" width="140">
            <template #default="{ row }">
              <span class="num-tabular">{{ row.sheet_count }} 表 · {{ row.row_count }}×{{ row.col_count }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="engine" label="识别引擎" width="160" />
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <span class="gem-pill" :class="row.status === 'demo' ? 'pill-gold' : 'pill-emerald'">
                {{ row.status === "demo" ? "演示" : "成功" }}
              </span>
            </template>
          </el-table-column>
          <el-table-column prop="created_at" label="时间 (UTC)" width="175" />
          <el-table-column label="操作" width="90">
            <template #default="{ row }">
              <el-button size="small" type="danger" text @click="removeHistory(row.id)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>

      <!-- ==================== FAQ 视图 ==================== -->
      <section v-else class="glass-strong" style="padding: 24px">
        <h2 class="section-heading">常见问题 (FAQ)</h2>
        <p class="section-sub">关于识别精度、数值格式保真与隐私安全的核心解答。</p>
        <div class="bento-grid">
          <SpotlightCard v-for="(faq, idx) in faqs" :key="faq.q">
            <span class="gem-pill pill-sky num-tabular" style="margin-bottom: 8px">Q{{ idx + 1 }}</span>
            <div style="font-weight: 700; font-size: 15px; margin-bottom: 6px">{{ faq.q }}</div>
            <p class="glass-muted" style="margin: 0; font-size: 13.5px; line-height: 1.65">{{ faq.a }}</p>
          </SpotlightCard>
        </div>
      </section>
    </main>

    <footer
      class="glass-muted"
      style="text-align: center; padding: 18px 16px 28px; font-size: 13px; border-top: 1px solid var(--glass-border)"
    >
      Image to Excel Studio · Vue 3 + FastAPI + Gemini Vision + openpyxl · 支持导出真实可编辑电子表格
    </footer>

    <CommandPalette v-model:visible="paletteVisible" :commands="commandItems" />
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useEventListener, useMagicKeys } from "@vueuse/core";
import { ElMessage } from "element-plus";
import { Grid, UploadFilled } from "@element-plus/icons-vue";

import SpotlightCard from "./components/SpotlightCard.vue";
import CommandPalette, { type CommandItem } from "./components/CommandPalette.vue";
import TableEditorPanel from "./components/TableEditorPanel.vue";
import { useTheme } from "./composables/useTheme";
import { prepareImageForUpload } from "./utils/imageCompressor";
import {
  deleteHistoryRecord,
  extractApiError,
  extractImages,
  fetchHealth,
  fetchHistory,
  fetchQuota,
  type ConversionRecord,
  type ExtractResponse,
} from "./api/convertApi";

interface QueuedImage {
  id: string;
  file: File;
  previewUrl: string;
}

const DRAFT_STORAGE_KEY = "i2e_editor_draft_v1";

const { theme, glass, activeTab } = useTheme();

const mode = ref<"excel" | "text">("excel");
const outputMode = ref<"all_in_one" | "per_image">("all_in_one");
const forceDemo = ref(false);
const customHint = ref("");
const isDragging = ref(false);
const extracting = ref(false);
const paletteVisible = ref(false);

const fileInputRef = ref<HTMLInputElement | null>(null);
const editorAnchorRef = ref<HTMLElement | null>(null);

const queue = ref<QueuedImage[]>([]);
const extractedResult = ref<ExtractResponse | null>(null);

const geminiConfigured = ref(false);
const activeModel = ref("gemini-4-hydrogen");
const quotaRemaining = ref(50);
const quotaLimit = ref(50);

const historyList = ref<ConversionRecord[]>([]);
const loadingHistory = ref(false);

const previewMap = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {};
  for (const item of queue.value) {
    map[item.file.name] = item.previewUrl;
  }
  return map;
});

// 快捷键 ⌘K / Ctrl+K 打开命令面板
const keys = useMagicKeys();
const cmdK = keys["Meta+K"];
const ctrlK = keys["Ctrl+K"];
watch([cmdK, ctrlK], ([m, c]) => {
  if (m || c) paletteVisible.value = true;
});

// 全局粘贴截图支持
useEventListener(window, "paste", (e: ClipboardEvent) => {
  if (activeTab.value !== "workspace") return;
  const items = e.clipboardData?.items;
  if (!items) return;
  const pastedFiles: File[] = [];
  for (const item of Array.from(items)) {
    if (item.type.startsWith("image/")) {
      const f = item.getAsFile();
      if (f) pastedFiles.push(f);
    }
  }
  if (pastedFiles.length > 0) {
    enqueueFiles(pastedFiles);
    ElMessage.success(`已从剪贴板粘贴 ${pastedFiles.length} 张图片`);
  }
});

// 自动持久化已提取编辑草稿，防止 F5 误刷丢失（R4）
watch(
  extractedResult,
  (val) => {
    if (val) {
      try {
        localStorage.setItem(DRAFT_STORAGE_KEY, JSON.stringify(val));
      } catch {
        // 忽略本地容量超限
      }
    } else {
      localStorage.removeItem(DRAFT_STORAGE_KEY);
    }
  },
  { deep: true }
);

function formatSize(bytes: number): string {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

function enqueueFiles(files: File[]) {
  const remainingSlots = 10 - queue.value.length;
  if (remainingSlots <= 0) {
    ElMessage.warning("单批最多支持 10 张图片，请先清空或提取当前队列");
    return;
  }
  const accepted = files.slice(0, remainingSlots);
  for (const f of accepted) {
    queue.value.push({
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
      file: f,
      previewUrl: URL.createObjectURL(f),
    });
  }
  if (files.length > remainingSlots) {
    ElMessage.warning(`超出单批 10 张限制，已截取前 ${remainingSlots} 张`);
  }
}

function onFileSelect(e: Event) {
  const input = e.target as HTMLInputElement;
  if (input.files?.length) {
    enqueueFiles(Array.from(input.files));
    input.value = "";
  }
}

function onDropFiles(e: DragEvent) {
  isDragging.value = false;
  if (e.dataTransfer?.files?.length) {
    enqueueFiles(Array.from(e.dataTransfer.files));
  }
}

function moveQueueItem(idx: number, delta: number) {
  const target = idx + delta;
  if (target < 0 || target >= queue.value.length) return;
  const arr = [...queue.value];
  [arr[idx], arr[target]] = [arr[target], arr[idx]];
  queue.value = arr;
}

function removeQueueItem(idx: number) {
  const [removed] = queue.value.splice(idx, 1);
  if (removed) URL.revokeObjectURL(removed.previewUrl);
}

function clearQueue() {
  for (const item of queue.value) URL.revokeObjectURL(item.previewUrl);
  queue.value = [];
}

async function loadSampleImage(sampleFilename: string) {
  try {
    const resp = await fetch(`/samples/${sampleFilename}`);
    if (!resp.ok) throw new Error("示例图加载失败");
    const blob = await resp.blob();
    const file = new File([blob], sampleFilename, { type: "image/png" });
    clearQueue();
    enqueueFiles([file]);
    ElMessage.success(`已添加示例图片：${sampleFilename}`);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  }
}

async function startExtraction() {
  if (queue.value.length === 0) return;
  extracting.value = true;
  try {
    const preparedFiles: File[] = [];
    for (const item of queue.value) {
      preparedFiles.push(await prepareImageForUpload(item.file));
    }
    const res = await extractImages({
      files: preparedFiles,
      mode: mode.value,
      outputMode: outputMode.value,
      forceDemo: forceDemo.value || !geminiConfigured.value,
      customHint: customHint.value,
    });
    extractedResult.value = res;
    quotaRemaining.value = res.quota.remaining;
    quotaLimit.value = res.quota.daily_limit;
    ElMessage.success(res.is_demo ? "演示结构化表格已生成，可直接编辑与导出" : "表格识别提取成功！");
    await nextTick();
    editorAnchorRef.value?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    extracting.value = false;
  }
}

function clearDraft() {
  extractedResult.value = null;
}

async function loadHistory() {
  loadingHistory.value = true;
  try {
    historyList.value = await fetchHistory();
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    loadingHistory.value = false;
  }
}

async function removeHistory(id: number) {
  try {
    await deleteHistoryRecord(id);
    historyList.value = historyList.value.filter((item) => item.id !== id);
    ElMessage.success("记录已删除");
  } catch (err) {
    ElMessage.error(extractApiError(err));
  }
}

function openHistoryTab() {
  activeTab.value = "history";
  loadHistory();
}

const commandItems = computed<CommandItem[]>(() => [
  {
    id: "sample-inv",
    label: "试玩示例：智能仓储库存盘点表",
    hint: "加载预置高清库存图片到上传队列",
    tag: "示例",
    action: () => {
      activeTab.value = "workspace";
      loadSampleImage("sample-inventory-table.png");
    },
  },
  {
    id: "sample-fin",
    label: "试玩示例：Q3 区域营收财务表",
    hint: "加载预置含货币与百分比的财务图片",
    tag: "示例",
    action: () => {
      activeTab.value = "workspace";
      loadSampleImage("sample-finance-report.png");
    },
  },
  {
    id: "theme-aurora",
    label: "切换主题：🌸 霞光毛玻璃",
    hint: "柔和暖粉流光浅色工作台",
    tag: "主题",
    action: () => (theme.value = "aurora"),
  },
  {
    id: "theme-nebula",
    label: "切换主题：🌌 星云毛玻璃",
    hint: "深邃宇宙蓝紫夜间护眼主题",
    tag: "主题",
    action: () => (theme.value = "nebula"),
  },
  {
    id: "theme-editorial",
    label: "切换主题：🏛️ 先锋杂志风",
    hint: "瑞士建筑网格高锐度极简风格",
    tag: "主题",
    action: () => (theme.value = "editorial"),
  },
  {
    id: "toggle-glass",
    label: glass.value ? "关闭背景模糊滤镜 (性能模式)" : "开启背景毛玻璃滤镜",
    hint: "低配核显或远程桌面可一键降级提升帧率",
    tag: "性能",
    action: () => (glass.value = !glass.value),
  },
  {
    id: "go-history",
    label: "查看转换记录",
    hint: "浏览历史提取的行列规模与引擎统计",
    tag: "导航",
    action: () => openHistoryTab(),
  },
]);

const guideSteps = [
  { title: "上传或粘贴表格图片", desc: "支持拖拽、本地浏览或直接按 Ctrl+V 粘贴剪贴板截图，单次最多 10 张。" },
  { title: "AI 结构化识别对齐", desc: "自动识别表头、数据行、货币/百分比类型及合并单元格，保留前导零编码。" },
  { title: "分栏对照与在线校对", desc: "左侧放大原图核对，右侧高亮提示疑似异常格，双击即可原地修改与增删行列。" },
  { title: "一键导出真 Excel", desc: "支持将多图合并为单工作簿多 Sheet，或逐图独立打包导出带格式 .xlsx / .csv。" },
];

const useCases = [
  { title: "财务报表与利润表", tag: "货币/百分比", pillClass: "pill-emerald", desc: "自动识别千分位金额与增长率，导出后可直接用于 Excel 求和与透视。" },
  { title: "仓储库存与 SKU 清单", tag: "保留前导零", pillClass: "pill-sky", desc: "对 001042 类前导零编码强制锁定文本列，避免 Excel 自动吞零。" },
  { title: "供应商采购报价单", tag: "多图合并", pillClass: "pill-violet", desc: "支持多页报价单照片批量上传，合并输出到一个多 Sheet .xlsx 文件。" },
  { title: "科研与实验统计表", tag: "等宽数字", pillClass: "pill-gold", desc: "表格数值列采用 JetBrains Mono 等宽排版，小数点对齐一目了然。" },
  { title: "纸质发票与费用明细", tag: "异常标黄", pillClass: "pill-coral", desc: "遇到拍糊、反光或手写涂改格，自动标记警示色提示人工二次确认。" },
  { title: "网页/PDF 表格截图", tag: "Ctrl+V 秒贴", pillClass: "pill-sky", desc: "截完图无需保存文件，切回页面直接 Ctrl+V 粘贴即可识别提取。" },
];

const faqs = [
  {
    q: "没有配置 GEMINI_API_KEY 也能试用吗？",
    a: "可以。系统内置了结构化演示兜底链路，未配置 Key 时点击「试玩示例」或上传图片即可体验完整分栏编辑与真实 .xlsx / .csv 导出；配置 GEMINI_API_KEY 后即可切换为真实视觉大模型识别。",
  },
  {
    q: "支持哪些图片格式？图片大小有限制吗？",
    a: "支持 JPG、PNG、GIF、WEBP、JFIF 与 HEIC，单张上限 10MB。超过 3.2MB 的超大图片在上传前会自动于浏览器端安全缩放，兼顾文字清晰度与云端 Serverless 响应速度。",
  },
  {
    q: "导出后的数字在 Excel 里可以直接用 SUM 求和吗？",
    a: "可以。我们在表格列头内置了语义类型识别（文本/数值/货币/百分比/日期），导出 .xlsx 时会自动将数值、货币和百分比写入真正的 Excel 数值格式，同时保护带前导零编号不被吞零。",
  },
  {
    q: "我上传的原始图片会被存到数据库吗？",
    a: "不会。上传图片仅在内存流中交给识别服务，数据库仅记录文件名、行列规模等轻量化统计摘要，既保护隐私又保证云端 Turso 数据库永不爆仓。",
  },
];

onMounted(async () => {
  // 恢复上次未关闭的编辑器草稿
  const savedDraft = localStorage.getItem(DRAFT_STORAGE_KEY);
  if (savedDraft) {
    try {
      extractedResult.value = JSON.parse(savedDraft);
    } catch {
      localStorage.removeItem(DRAFT_STORAGE_KEY);
    }
  }

  try {
    const [health, quota] = await Promise.all([fetchHealth(), fetchQuota()]);
    geminiConfigured.value = health.gemini_configured;
    activeModel.value = health.model;
    quotaRemaining.value = quota.remaining;
    quotaLimit.value = quota.daily_limit;
  } catch {
    // 后端未启动时保持界面可用
  }

  if (activeTab.value === "history") {
    loadHistory();
  }
});
</script>
