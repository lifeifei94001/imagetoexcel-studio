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
        <span v-if="b2Configured" class="gem-pill pill-sky">Backblaze B2 已挂载</span>
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
          <h1 class="hero-title">将报价单与表格图片转为可编辑 Excel</h1>
          <p class="hero-subtitle">
            支持批量上传多张报价单长图。每张图自动合并为 1 份完整报价单 Sheet，后台分片进度运行、结果自动归档至
            Backblaze 对象存储，可单表导出 <code>.xlsx</code> 或一键打包 <code>.zip</code>。
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

        <!-- 双栏队列式上传工作台 -->
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
                拖拽多张报价单图片到此处、点击浏览，或 <kbd>Ctrl+V</kbd> 粘贴截图
              </div>
              <div class="glass-muted" style="font-size: 13px">
                支持 JPG, PNG, GIF, WEBP, JFIF, HEIC · 单张上限 10MB（单批最多 20 张长图，保宽高清直传）
              </div>
              <div style="display: flex; gap: 10px; flex-wrap: wrap; justify-content: center; margin-top: 6px" @click.stop>
                <el-button type="primary" @click="fileInputRef?.click()">选择图片文件</el-button>
                <el-button @click="loadSampleImages(['sample-inventory-table.png'])">
                  试玩：单张库存表
                </el-button>
                <el-button @click="loadSampleImages(['sample-inventory-table.png', 'sample-finance-report.png'])">
                  试玩：多图批量打包
                </el-button>
              </div>
            </div>

            <!-- 右栏：待转换队列 + 报价单模式配置 + 转换按钮 -->
            <div class="queue-panel">
              <div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px">
                  <span style="font-weight: 700; font-size: 14px">
                    待处理队列 (<span class="num-tabular">{{ queue.length }}</span>/20)
                  </span>
                  <el-checkbox v-if="!geminiConfigured" v-model="forceDemo" size="small" disabled>
                    演示提取模式
                  </el-checkbox>
                  <el-checkbox v-else v-model="forceDemo" size="small">
                    演示模式试玩
                  </el-checkbox>
                </div>

                <div
                  v-if="queue.length === 0"
                  class="glass-muted"
                  style="
                    padding: 26px 12px;
                    text-align: center;
                    font-size: 13px;
                    border: 1px dashed var(--glass-border);
                    border-radius: var(--glass-radius-sm);
                  "
                >
                  暂无图片。可一次选中多张报价单，或直接 <kbd>Ctrl+V</kbd> 连续粘贴多张截图。
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
                  placeholder="可选识别提示（如：保留前导零、价格按列对齐、备注放提示栏）"
                />

                <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px">
                  <span style="font-size: 12.5px; font-weight: 600">单图合表（报价单模式）：</span>
                  <el-switch
                    v-model="mergePerImage"
                    active-text="1图合为1个Sheet"
                    inactive-text="保留多子表"
                  />
                </div>

                <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px">
                  <span style="font-size: 12.5px; font-weight: 600">多图默认打包：</span>
                  <el-radio-group v-model="outputMode" size="small">
                    <el-radio value="per_image">一图一表 (多图打包 .zip)</el-radio>
                    <el-radio value="all_in_one">合并多 Sheet</el-radio>
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
                    @click="startBatchExtraction"
                  >
                    {{ extracting ? `正在后台分片处理 (${doneCount}/${progressItems.length})...` : "开始提取并归档对象存储" }}
                  </el-button>
                </div>
              </div>
            </div>
          </div>

          <div class="glass-muted" style="text-align: center; font-size: 12.5px; margin-top: 12px">
            * 批量任务采用单图独立分片执行，长报价单最长支持 40 秒深度提取；成品自动存入 Backblaze B2，可随时在「转换记录」下载。
          </div>
        </section>

        <!-- 实时批处理进度条面板（多张报价单时无需干等，可查看每张进度与 B2 归档状态） -->
        <section v-if="progressItems.length > 0" class="glass-strong" style="padding: 18px 20px; margin-bottom: 22px">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 8px">
            <div style="display: flex; align-items: center; gap: 10px">
              <span style="font-weight: 700; font-size: 15px">批处理任务进度</span>
              <span class="gem-pill" :class="extracting ? 'pill-sky' : 'pill-emerald'">
                {{ extracting ? `处理中 ${doneCount} / ${progressItems.length}` : `全部完成 ${doneCount} / ${progressItems.length}` }}
              </span>
            </div>
            <div style="display: flex; gap: 8px">
              <el-button v-if="!extracting && lastBatchJobUid" size="small" type="primary" plain @click="downloadLastJobPackage">
                从 B2 下载本批成品
              </el-button>
              <el-button v-if="!extracting" size="small" text @click="progressItems = []">收起进度</el-button>
            </div>
          </div>

          <el-progress
            :percentage="progressPercent"
            :status="extracting ? undefined : (failedCount > 0 ? 'warning' : 'success')"
            :stroke-width="10"
            style="margin-bottom: 12px"
          />

          <div v-auto-animate style="display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 10px">
            <div v-for="pItem in progressItems" :key="pItem.id" class="queue-item">
              <div style="min-width: 0">
                <div style="font-weight: 600; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                  {{ pItem.quoteTitle || pItem.filename }}
                </div>
                <div class="glass-muted" style="font-size: 12px">
                  {{ pItem.message }}
                </div>
              </div>
              <span
                class="gem-pill"
                :class="
                  pItem.status === 'completed'
                    ? 'pill-emerald'
                    : pItem.status === 'processing'
                    ? 'pill-sky'
                    : pItem.status === 'failed'
                    ? 'pill-coral'
                    : 'pill-gold'
                "
              >
                {{
                  pItem.status === "completed"
                    ? "已完成"
                    : pItem.status === "processing"
                    ? "识别中"
                    : pItem.status === "failed"
                    ? "失败"
                    : "排队中"
                }}
              </span>
            </div>
          </div>
        </section>

        <!-- 分栏交互式编辑器 -->
        <div ref="editorAnchorRef">
          <TableEditorPanel
            v-if="quotations.length > 0"
            v-model:quotations="quotations"
            v-model:merge-per-image="mergePerImage"
            :preview-map="previewMap"
            :mode="mode"
            @clear-draft="clearDraft"
          />
        </div>

        <!-- 高频场景 Bento 卡片 -->
        <section style="margin-top: 32px">
          <h2 class="section-heading">专为报价单与长图表格打造的增强能力</h2>
          <p class="section-sub">从手机长截图到多商家比价归档，一次配置全自动跑完。</p>
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
        <h2 class="section-heading">4 步批量搞定报价单图片转 Excel</h2>
        <p class="section-sub">单图自动合表 + 多图进度后台归档至 Backblaze B2，再也不必守在电脑前。</p>
        <div class="bento-grid">
          <SpotlightCard v-for="(step, idx) in guideSteps" :key="step.title">
            <span class="gem-pill pill-violet" style="margin-bottom: 10px">STEP 0{{ idx + 1 }}</span>
            <div style="font-weight: 700; font-size: 15.5px; margin-bottom: 6px">{{ step.title }}</div>
            <p class="glass-muted" style="margin: 0; font-size: 13.5px; line-height: 1.65">{{ step.desc }}</p>
          </SpotlightCard>
        </div>
        <div style="margin-top: 12px">
          <el-button type="primary" @click="activeTab = 'workspace'">返回工作台开始批量处理</el-button>
        </div>
      </section>

      <!-- ==================== 转换记录视图（含 Backblaze B2 云端批次存档下载 + 载入复编） ==================== -->
      <section v-else-if="activeTab === 'history'" style="display: flex; flex-direction: column; gap: 18px">
        <div class="glass-strong" style="padding: 22px">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 10px">
            <div>
              <h2 class="section-heading">批量任务与 Backblaze 对象存储存档</h2>
              <p class="section-sub" style="margin-bottom: 0">
                每次处理完的独立报价单 <code>.xlsx</code>、多图汇总 <code>.zip</code> 与结构化数据均持久化至 Backblaze B2，可随时下载或载入编辑器复改。
              </p>
            </div>
            <el-button size="small" :loading="loadingHistory" @click="loadHistory">刷新全部记录</el-button>
          </div>

          <el-empty v-if="batchJobs.length === 0" description="暂无批次任务记录，去工作台上传报价单试试吧" :image-size="72" />

          <div v-else v-auto-animate style="display: flex; flex-direction: column; gap: 14px">
            <div v-for="job in batchJobs" :key="job.job_uid" class="glass" style="padding: 16px">
              <div style="display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 10px">
                <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
                  <span style="font-weight: 700; font-size: 15px">{{ job.title }}</span>
                  <span class="gem-pill" :class="job.storage_backend === 'b2' ? 'pill-sky' : 'pill-gold'">
                    {{ job.storage_backend === "b2" ? "Backblaze B2" : "本地缓存" }}
                  </span>
                  <span class="gem-pill" :class="job.status === 'completed' ? 'pill-emerald' : 'pill-gold'">
                    {{ job.completed_items }}/{{ job.total_items }} 份 · {{ job.status }}
                  </span>
                  <span class="glass-muted num-tabular" style="font-size: 12px">{{ job.created_at }}</span>
                </div>

                <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
                  <el-button
                    v-if="job.json_b2_key"
                    size="small"
                    :loading="loadingJobUid === job.job_uid"
                    @click="restoreJobToEditor(job.job_uid)"
                  >
                    载入工作台编辑
                  </el-button>
                  <el-button
                    v-if="job.zip_b2_key || job.items.length === 1"
                    size="small"
                    type="primary"
                    :loading="downloadingKey === job.job_uid"
                    @click="downloadStoredArtifact(job.job_uid, undefined, job.zip_filename || job.items[0]?.xlsx_filename || '报价单.xlsx')"
                  >
                    {{ job.zip_b2_key ? "下载整批打包 (.zip)" : "下载报价单 (.xlsx)" }}
                  </el-button>
                  <el-button size="small" type="danger" text @click="removeBatchJob(job.job_uid)">删除</el-button>
                </div>
              </div>

              <!-- 批次内每张报价单明细 -->
              <div v-if="job.items.length > 0" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(270px, 1fr)); gap: 8px">
                <div v-for="item in job.items" :key="item.id" class="queue-item">
                  <div style="min-width: 0">
                    <div style="font-weight: 600; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                      {{ item.quote_title || item.source_filename }}
                    </div>
                    <div class="glass-muted num-tabular" style="font-size: 12px">
                      {{ item.row_count }}×{{ item.col_count }} · {{ item.engine }}
                    </div>
                  </div>
                  <el-button
                    v-if="item.status === 'completed' && item.xlsx_b2_key"
                    size="small"
                    plain
                    :loading="downloadingKey === `${job.job_uid}-${item.id}`"
                    @click="downloadStoredArtifact(job.job_uid, item.id, item.xlsx_filename)"
                  >
                    下载 .xlsx
                  </el-button>
                  <span v-else class="gem-pill pill-coral">失败</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 单文件调用流水表 -->
        <div class="glass-strong" style="padding: 22px">
          <h3 class="section-heading" style="font-size: 1.15rem">单张识别调用明细</h3>
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
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <span class="gem-pill" :class="row.status === 'demo' ? 'pill-gold' : 'pill-emerald'">
                  {{ row.status === "demo" ? "演示" : "成功" }}
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="created_at" label="时间 (UTC)" width="175" />
            <el-table-column label="操作" width="80">
              <template #default="{ row }">
                <el-button size="small" type="danger" text @click="removeHistory(row.id)">删除</el-button>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </section>

      <!-- ==================== FAQ 视图 ==================== -->
      <section v-else class="glass-strong" style="padding: 24px">
        <h2 class="section-heading">常见问题 (FAQ)</h2>
        <p class="section-sub">关于报价单合表、长图保真、Backblaze B2 云端下载的常见问题。</p>
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
      Image to Excel Studio · Vue 3 + FastAPI + Gemini Vision + Backblaze B2 + openpyxl
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
  createBatchJob,
  deleteBatchJob,
  deleteHistoryRecord,
  downloadJobArtifact,
  extractApiError,
  fetchBatchJobs,
  fetchHealth,
  fetchHistory,
  fetchQuota,
  finalizeBatchJob,
  loadJobIntoEditor,
  processBatchJobItem,
  type BatchJob,
  type ConversionRecord,
  type QuotationGroup,
} from "./api/convertApi";

interface QueuedImage {
  id: string;
  file: File;
  previewUrl: string;
}

interface ProgressEntry {
  id: string;
  filename: string;
  quoteTitle: string;
  status: "pending" | "processing" | "completed" | "failed";
  message: string;
}

const DRAFT_STORAGE_KEY = "i2e_editor_draft_v2";

const { theme, glass, activeTab } = useTheme();

const mode = ref<"excel" | "text">("excel");
const outputMode = ref<"all_in_one" | "per_image">("per_image");
const mergePerImage = ref(true);
const forceDemo = ref(false);
const customHint = ref("");
const isDragging = ref(false);
const extracting = ref(false);
const paletteVisible = ref(false);

const fileInputRef = ref<HTMLInputElement | null>(null);
const editorAnchorRef = ref<HTMLElement | null>(null);

const queue = ref<QueuedImage[]>([]);
const quotations = ref<QuotationGroup[]>([]);
const progressItems = ref<ProgressEntry[]>([]);
const lastBatchJobUid = ref("");

const geminiConfigured = ref(false);
const b2Configured = ref(false);
const activeModel = ref("gemini-4-hydrogen");
const quotaRemaining = ref(50);
const quotaLimit = ref(50);

const batchJobs = ref<BatchJob[]>([]);
const historyList = ref<ConversionRecord[]>([]);
const loadingHistory = ref(false);
const downloadingKey = ref("");
const loadingJobUid = ref("");

const previewMap = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {};
  for (const item of queue.value) {
    map[item.file.name] = item.previewUrl;
  }
  return map;
});

const doneCount = computed(
  () => progressItems.value.filter((i) => i.status === "completed" || i.status === "failed").length
);
const failedCount = computed(() => progressItems.value.filter((i) => i.status === "failed").length);
const progressPercent = computed(() => {
  if (progressItems.value.length === 0) return 0;
  return Math.round((doneCount.value / progressItems.value.length) * 100);
});

const keys = useMagicKeys();
const cmdK = keys["Meta+K"];
const ctrlK = keys["Ctrl+K"];
watch([cmdK, ctrlK], ([m, c]) => {
  if (m || c) paletteVisible.value = true;
});

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

watch(
  quotations,
  (val) => {
    if (val.length > 0) {
      try {
        localStorage.setItem(
          DRAFT_STORAGE_KEY,
          JSON.stringify({ mergePerImage: mergePerImage.value, quotations: val })
        );
      } catch {
        // 忽略容量限制
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
  const remainingSlots = 20 - queue.value.length;
  if (remainingSlots <= 0) {
    ElMessage.warning("单批最多支持 20 张报价单图片，请先清空或处理当前队列");
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
    ElMessage.warning(`超出单批 20 张限制，已截取前 ${remainingSlots} 张`);
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

async function loadSampleImages(sampleNames: string[]) {
  try {
    clearQueue();
    const files: File[] = [];
    for (const sName of sampleNames) {
      const resp = await fetch(`/samples/${sName}`);
      if (!resp.ok) throw new Error(`示例图加载失败：${sName}`);
      const blob = await resp.blob();
      files.push(new File([blob], sName, { type: "image/png" }));
    }
    enqueueFiles(files);
    ElMessage.success(`已添加 ${files.length} 张示例报价单到队列`);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  }
}

async function startBatchExtraction() {
  if (queue.value.length === 0 || extracting.value) return;
  extracting.value = true;
  lastBatchJobUid.value = "";
  const snapshot = [...queue.value];

  progressItems.value = snapshot.map((q) => ({
    id: q.id,
    filename: q.file.name,
    quoteTitle: "",
    status: "pending",
    message: "等待分片调度...",
  }));

  try {
    const batchTitle =
      snapshot.length === 1
        ? snapshot[0].file.name.replace(/\.[^.]+$/, "")
        : `批量报价单_${snapshot.length}份`;

    const createdJob = await createBatchJob({
      title: batchTitle,
      mode: mode.value,
      outputMode: outputMode.value,
      mergePerImage: mergePerImage.value,
      totalItems: snapshot.length,
    });
    lastBatchJobUid.value = createdJob.job_uid;

    const collected: Array<QuotationGroup | null> = new Array(snapshot.length).fill(null);
    let cursor = 0;
    const concurrency = Math.min(2, snapshot.length);

    const worker = async () => {
      while (cursor < snapshot.length) {
        const myIdx = cursor++;
        const qItem = snapshot[myIdx];
        progressItems.value[myIdx].status = "processing";
        progressItems.value[myIdx].message = "正在进行长图 AI 结构识别并归档 B2...";
        try {
          const prepared = await prepareImageForUpload(qItem.file);
          const res = await processBatchJobItem({
            jobUid: createdJob.job_uid,
            file: prepared,
            itemIndex: myIdx,
            mode: mode.value,
            forceDemo: forceDemo.value,
            customHint: customHint.value,
            mergePerImage: mergePerImage.value,
          });
          collected[myIdx] = res.quotation;
          if (!res.quotation.is_demo) {
            geminiConfigured.value = true;
            activeModel.value = res.quotation.engine;
          }
          quotaRemaining.value = res.quota.remaining;
          quotaLimit.value = res.quota.daily_limit;

          progressItems.value[myIdx].status = "completed";
          progressItems.value[myIdx].quoteTitle = res.quotation.quote_title;
          progressItems.value[myIdx].message = `识别完成 (${res.quotation.merged_sheet.rows.length} 行 · 已存对象存储)`;

          // 实时把已完成报价单展示进工作台
          quotations.value = collected.filter((x): x is QuotationGroup => x !== null);
        } catch (err) {
          progressItems.value[myIdx].status = "failed";
          progressItems.value[myIdx].message = extractApiError(err);
        }
      }
    };

    await Promise.all(Array.from({ length: concurrency }, () => worker()));

    const validQuotations = collected.filter((x): x is QuotationGroup => x !== null);
    quotations.value = validQuotations;

    if (validQuotations.length > 0) {
      await finalizeBatchJob({
        jobUid: createdJob.job_uid,
        quotations: validQuotations,
        mergePerImage: mergePerImage.value,
        outputMode: outputMode.value,
      });
      ElMessage.success(
        validQuotations.length === snapshot.length
          ? `全部 ${validQuotations.length} 份报价单已提取并归档！`
          : `已完成 ${validQuotations.length}/${snapshot.length} 份（部分失败可查看进度说明）`
      );
      await nextTick();
      editorAnchorRef.value?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      await finalizeBatchJob({
        jobUid: createdJob.job_uid,
        quotations: [],
        mergePerImage: mergePerImage.value,
        outputMode: outputMode.value,
      });
      ElMessage.error("本批次图片均未能提取成功，请检查进度栏错误原因");
    }
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    extracting.value = false;
  }
}

async function downloadLastJobPackage() {
  if (!lastBatchJobUid.value) return;
  await downloadStoredArtifact(
    lastBatchJobUid.value,
    undefined,
    quotations.value.length > 1 ? "报价单批量打包.zip" : `${quotations.value[0]?.quote_title || "报价单"}.xlsx`
  );
}

function clearDraft() {
  quotations.value = [];
}

async function loadHistory() {
  loadingHistory.value = true;
  try {
    const [jobs, records] = await Promise.all([fetchBatchJobs(), fetchHistory()]);
    batchJobs.value = jobs;
    historyList.value = records;
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    loadingHistory.value = false;
  }
}

async function downloadStoredArtifact(jobUid: string, itemId: number | undefined, fallbackName: string) {
  const key = itemId !== undefined ? `${jobUid}-${itemId}` : jobUid;
  downloadingKey.value = key;
  try {
    const dlName = await downloadJobArtifact({ jobUid, itemId, fallbackFilename: fallbackName });
    ElMessage.success(`已从对象存储下载：${dlName}`);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    downloadingKey.value = "";
  }
}

async function restoreJobToEditor(jobUid: string) {
  loadingJobUid.value = jobUid;
  try {
    const data = await loadJobIntoEditor(jobUid);
    mergePerImage.value = data.merge_per_image;
    outputMode.value = data.output_mode;
    quotations.value = data.quotations;
    activeTab.value = "workspace";
    ElMessage.success(`已载入 ${data.quotations.length} 份报价单到编辑工作台`);
    await nextTick();
    editorAnchorRef.value?.scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    loadingJobUid.value = "";
  }
}

async function removeBatchJob(jobUid: string) {
  try {
    await deleteBatchJob(jobUid);
    batchJobs.value = batchJobs.value.filter((j) => j.job_uid !== jobUid);
    ElMessage.success("批次记录已删除");
  } catch (err) {
    ElMessage.error(extractApiError(err));
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
    id: "sample-multi",
    label: "试玩示例：多图报价单批量打包",
    hint: "同时载入库存表与财务表两张图片测试批量打包 .zip",
    tag: "示例",
    action: () => {
      activeTab.value = "workspace";
      loadSampleImages(["sample-inventory-table.png", "sample-finance-report.png"]);
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
    label: "查看转换记录与 B2 下载",
    hint: "下载云端已完成的 .xlsx / .zip 或重新载入编辑",
    tag: "导航",
    action: () => openHistoryTab(),
  },
]);

const guideSteps = [
  { title: "批量拖入或粘贴报价单", desc: "支持一次选入多达 20 张长图报价单，<=3.6MB 原像素直传，超大图保宽不压糊。" },
  { title: "单图多区块自动合为 1 表", desc: "同张图片内按品牌/系列拆开的区块，自动补充「分类/区块」列合并为统一主表头的单一 Sheet。" },
  { title: "进度条运行 + 自动存 B2", desc: "分片调度避免长图超时，无需盯着等待；成品单表 .xlsx 与整批 .zip 自动上传至 Backblaze B2。" },
  { title: "随取随下或单表/打包导出", desc: "工作台可导出单份 .xlsx 或全批 .zip，也可在「转换记录」随时下载或载入复改。" },
];

const useCases = [
  { title: "1 图 = 1 完整报价单 Sheet", tag: "智能合表", pillClass: "pill-emerald", desc: "多系列区块自动合进同一 Sheet，首列带出系列名称，方便直接筛选与 VLOOKUP。" },
  { title: "单份导出与批量打包 .zip", tag: "多图批处理", pillClass: "pill-sky", desc: "支持单份报价单独立下载 .xlsx，多张报价单时一键打包成平铺 .zip 压缩包。" },
  { title: "长图保宽清晰 + 300% 放大", tag: "长截图友好", pillClass: "pill-violet", desc: "保宽不截长策略保护竖屏长报价单数字锐利度，左侧对照窗支持最高 300% 缩放。" },
  { title: "Backblaze B2 永久云端存档", tag: "对象存储", pillClass: "pill-gold", desc: "批量任务处理完自动持久化 .xlsx、.zip 与结构化数据到 B2，转换记录页随时回溯下载。" },
  { title: "商家规则备注单独提取", tag: "纯净数据行", pillClass: "pill-coral", desc: "顶部时效与扣差说明单独展示在提示栏，不污染数据行，数值列保留真数字。" },
  { title: "无损切换合表 / 拆分子表", tag: "零额外消耗", pillClass: "pill-sky", desc: "保留原始子表结构，工作台内随时切换合表或拆表视图，无需重新调用 AI。" },
];

const faqs = [
  {
    q: "为什么多张报价单改用「分片进度调度」？",
    a: "超长报价单识别往往需要 15~40 秒。若多张图挤在同一个 HTTP 请求里容易触发 Vercel 60 秒上限；改成单图分片（每图独享 40 秒 AI 预算）+ 进度条后，既稳定又不会因为某一张超时拖垮整批。",
  },
  {
    q: "Backblaze B2 存储桶里保存了哪些文件？",
    a: "每个完成批次会在存储桶 image-to-execl 中保存每张报价单的独立 .xlsx、多图时的汇总 .zip 压缩包，以及供「载入工作台编辑」使用的轻量化 JSON；原始图片不长期占用你的存储空间。",
  },
  {
    q: "合并成一个 Sheet 后还能查看原来的分块吗？",
    a: "可以。我们在编辑区右上角保留了「合为单表 / 拆分子表」开关，切换即可看原始拆分的每个区块子表，不会重新消耗 API 次数。",
  },
  {
    q: "报价单里的区间价或 +50 加减价会被当成公式吗？",
    a: "不会。纯数字价格（如 3400、¥2,199.00）会转为真正的 Excel 数值格式方便求和；带有连字符区间（如 3200-3300）或显式加号（+50）的文本会自动保持文本原样。",
  },
];

onMounted(async () => {
  const savedDraft = localStorage.getItem(DRAFT_STORAGE_KEY);
  if (savedDraft) {
    try {
      const parsed = JSON.parse(savedDraft);
      if (Array.isArray(parsed?.quotations) && parsed.quotations.length > 0) {
        mergePerImage.value = Boolean(parsed.mergePerImage ?? true);
        quotations.value = parsed.quotations;
      }
    } catch {
      localStorage.removeItem(DRAFT_STORAGE_KEY);
    }
  }

  try {
    const [health, quota] = await Promise.all([fetchHealth(), fetchQuota()]);
    geminiConfigured.value = health.gemini_configured;
    b2Configured.value = Boolean(health.b2_configured);
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
