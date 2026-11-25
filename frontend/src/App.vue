<template>
  <div class="app-shell">
    <!-- 登录态检测骨架屏 -->
    <div
      v-if="authChecking"
      style="min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px"
    >
      <div class="glass-strong" style="padding: 28px 36px; text-align: center; max-width: 380px; width: 100%">
        <div class="brand-icon" style="margin: 0 auto 14px">
          <el-icon :size="20"><Grid /></el-icon>
        </div>
        <div style="font-weight: 700; font-size: 16px; margin-bottom: 6px">Image to Excel Studio</div>
        <div class="glass-muted" style="font-size: 13px">正在校验访问权限...</div>
      </div>
    </div>

    <!-- 全站登录门禁（未登录时拦截访问，防止他人盗刷额度与对象存储） -->
    <div
      v-else-if="authRequired && !authenticated"
      style="min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px"
    >
      <div class="glass-strong" style="padding: 34px 32px; max-width: 420px; width: 100%">
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px">
          <div style="display: flex; align-items: center; gap: 10px">
            <span class="brand-icon">
              <el-icon :size="18"><Lock /></el-icon>
            </span>
            <div>
              <div style="font-weight: 800; font-size: 17px">Image to Excel Studio</div>
              <div class="glass-muted" style="font-size: 12px">私有报价单 AI 提取工作台</div>
            </div>
          </div>
          <el-select v-model="theme" size="small" style="width: 112px" aria-label="切换主题">
            <el-option label="🌸 霞光" value="aurora" />
            <el-option label="🌌 星云" value="nebula" />
            <el-option label="🏛️ 杂志" value="editorial" />
          </el-select>
        </div>

        <el-alert
          v-if="!authConfigured"
          type="warning"
          show-icon
          :closable="false"
          title="服务端尚未配置 AUTH_USERNAME / AUTH_PASSWORD"
          description="请在 Vercel 项目环境变量中配置登录账号与密码后重新部署。"
          style="margin-bottom: 16px"
        />

        <el-alert
          v-if="loginError"
          type="error"
          show-icon
          :closable="false"
          :title="loginError"
          style="margin-bottom: 16px"
        />

        <form style="display: flex; flex-direction: column; gap: 14px" @submit.prevent="handleLogin">
          <div>
            <label style="display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px">账号</label>
            <el-input
              v-model="loginUsername"
              size="large"
              placeholder="请输入管理员用户名"
              autocomplete="username"
              clearable
            />
          </div>

          <div>
            <label style="display: block; font-size: 13px; font-weight: 600; margin-bottom: 6px">密码</label>
            <el-input
              v-model="loginPassword"
              type="password"
              size="large"
              show-password
              placeholder="请输入登录密码"
              autocomplete="current-password"
              @keyup.enter="handleLogin"
            />
          </div>

          <el-button
            type="primary"
            size="large"
            native-type="submit"
            :loading="loggingIn"
            :disabled="!loginUsername.trim() || !loginPassword"
            style="width: 100%; margin-top: 6px"
          >
            {{ loggingIn ? "正在验证..." : "登录工作台" }}
          </el-button>
        </form>

        <div class="glass-muted" style="font-size: 12px; text-align: center; margin-top: 16px; line-height: 1.6">
          登录成功后当前浏览器将保持 7 天免密状态，可随时在右上角退出登录。
        </div>
      </div>
    </div>

    <!-- 已认证主工作台 -->
    <template v-else>
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

        <template v-if="authRequired">
          <span class="gem-pill pill-violet">{{ currentUsername || "admin" }}</span>
          <el-button size="small" type="danger" plain @click="handleLogout">退出</el-button>
        </template>
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
                  <span style="font-size: 12.5px; font-weight: 600">右侧扣费备注列：</span>
                  <el-switch
                    v-model="pricesOnly"
                    active-text="仅提机型+价格(更快)"
                    inactive-text="完整保留扣费备注"
                  />
                </div>

                <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 8px">
                  <span style="font-size: 12.5px; font-weight: 600">识别模型档位：</span>
                  <el-radio-group v-model="modelTier" size="small">
                    <el-radio value="fast">极速稳定 (Flash Lite)</el-radio>
                    <el-radio value="accurate">旗舰精读 (Hydrogen)</el-radio>
                  </el-radio-group>
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

      <!-- ==================== 转换记录视图（含 Backblaze B2 云端批次存档下载 + 一键清空释放空间） ==================== -->
      <section v-else-if="activeTab === 'history'" style="display: flex; flex-direction: column; gap: 18px">
        <div class="glass-strong" style="padding: 22px">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 10px">
            <div>
              <div style="display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 4px">
                <h2 class="section-heading" style="margin: 0">批量任务与 Backblaze 对象存储存档</h2>
                <span v-if="storageStats" class="gem-pill pill-sky num-tabular">
                  {{ storageStats.backend === "b2" ? "B2 存储桶" : "本地缓存" }}：{{ storageStats.file_count }} 个版本 ·
                  {{ formatSize(storageStats.total_bytes) }}
                </span>
              </div>
              <p class="section-sub" style="margin-bottom: 0">
                每次处理完的独立报价单 <code>.xlsx</code>、多图汇总 <code>.zip</code> 与结构化数据均持久化至 Backblaze B2；删除或一键清空将同步彻底清理云端文件及历史版本，释放 10GB 免费额度。
              </p>
            </div>
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
              <el-button size="small" :loading="loadingHistory" @click="loadHistory">刷新记录</el-button>
              <el-button
                size="small"
                type="warning"
                plain
                :loading="purgingB2"
                @click="confirmPurgeB2"
              >
                一键释放 B2 空间
              </el-button>
              <el-button
                size="small"
                type="danger"
                :loading="clearingAll"
                :disabled="batchJobs.length === 0 && historyList.length === 0 && (storageStats?.file_count ?? 0) === 0"
                @click="confirmClearAllRecords"
              >
                一键清空全部记录
              </el-button>
            </div>
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
                  <span class="glass-muted num-tabular" style="font-size: 12px">{{ formatBeijingTime(job.created_at) }}</span>
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
                    v-if="job.zip_b2_key || (job.items.length === 1 && job.items[0]?.xlsx_b2_key)"
                    size="small"
                    type="primary"
                    :loading="downloadingKey === job.job_uid"
                    @click="downloadStoredArtifact(job.job_uid, undefined, job.zip_filename || job.items[0]?.xlsx_filename || '报价单.xlsx')"
                  >
                    {{ job.zip_b2_key ? "下载整批打包 (.zip)" : "下载报价单 (.xlsx)" }}
                  </el-button>
                  <el-button size="small" type="danger" text @click="removeBatchJob(job.job_uid)">删除并释放</el-button>
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
                  <span v-else-if="item.status === 'completed'" class="gem-pill pill-gold">云端已清理</span>
                  <span v-else class="gem-pill pill-coral">失败</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 单文件调用流水表 -->
        <div class="glass-strong" style="padding: 22px">
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-wrap: wrap; gap: 8px">
            <h3 class="section-heading" style="font-size: 1.15rem; margin: 0">单张识别调用明细</h3>
            <span class="glass-muted" style="font-size: 12.5px">共 {{ historyList.length }} 条记录（点击上方「一键清空全部记录」可一并删除）</span>
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
            <el-table-column label="状态" width="90">
              <template #default="{ row }">
                <span class="gem-pill" :class="row.status === 'demo' ? 'pill-gold' : 'pill-emerald'">
                  {{ row.status === "demo" ? "演示" : "成功" }}
                </span>
              </template>
            </el-table-column>
            <el-table-column label="时间 (北京时间)" width="175">
              <template #default="{ row }">
                <span class="num-tabular">{{ formatBeijingTime(row.created_at) }}</span>
              </template>
            </el-table-column>
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
    </template>
  </div>
</template>

<script setup lang="ts">
import axios from "axios";
import { computed, nextTick, onMounted, ref, watch } from "vue";
import { useEventListener, useMagicKeys } from "@vueuse/core";
import { ElMessage, ElMessageBox } from "element-plus";
import { Grid, Lock, UploadFilled } from "@element-plus/icons-vue";

import SpotlightCard from "./components/SpotlightCard.vue";
import CommandPalette, { type CommandItem } from "./components/CommandPalette.vue";
import TableEditorPanel from "./components/TableEditorPanel.vue";
import { useTheme } from "./composables/useTheme";
import { splitLongImageSmart } from "./utils/imageCompressor";
import {
  clearAllRecords,
  clearStoredAuthToken,
  commitSlicedQuotation,
  createBatchJob,
  deleteBatchJob,
  deleteHistoryRecord,
  downloadJobArtifact,
  extractApiError,
  extractSlice,
  fetchAuthStatus,
  fetchBatchJobs,
  fetchHealth,
  fetchHistory,
  fetchQuota,
  fetchStorageStats,
  finalizeBatchJob,
  loadJobIntoEditor,
  loginWithPassword,
  processBatchJobItem,
  purgeB2Storage,
  setUnauthorizedHandler,
  type BatchJob,
  type ConversionRecord,
  type QuotationGroup,
  type StorageStats,
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
const pricesOnly = ref(false);
const modelTier = ref<"fast" | "accurate">("fast");
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
const activeModel = ref("gemini-3.5-flash-lite");
const quotaRemaining = ref(50);
const quotaLimit = ref(50);

// 登录鉴权状态
const authChecking = ref(true);
const authRequired = ref(false);
const authConfigured = ref(true);
const authenticated = ref(false);
const currentUsername = ref("");
const loginUsername = ref("admin");
const loginPassword = ref("");
const loggingIn = ref(false);
const loginError = ref("");

const batchJobs = ref<BatchJob[]>([]);
const historyList = ref<ConversionRecord[]>([]);
const storageStats = ref<StorageStats | null>(null);
const loadingHistory = ref(false);
const clearingAll = ref(false);
const purgingB2 = ref(false);
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

function formatBeijingTime(utcStr?: string): string {
  if (!utcStr) return "-";
  const normalized = utcStr.includes("T") ? utcStr : utcStr.replace(" ", "T");
  const iso = normalized.endsWith("Z") ? normalized : `${normalized}Z`;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return utcStr;
  const bj = new Date(d.getTime() + 8 * 3600 * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${bj.getUTCFullYear()}-${pad(bj.getUTCMonth() + 1)}-${pad(bj.getUTCDate())} ${pad(bj.getUTCHours())}:${pad(bj.getUTCMinutes())}:${pad(bj.getUTCSeconds())}`;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
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
          const slicePlan = await splitLongImageSmart(qItem.file);
          const slices = slicePlan.files;
          if (slices.length <= 1) {
            const targetFile = slices[0] || qItem.file;
            const res = await processBatchJobItem({
              jobUid: createdJob.job_uid,
              file: targetFile,
              itemIndex: myIdx,
              mode: mode.value,
              forceDemo: forceDemo.value,
              customHint: customHint.value,
              mergePerImage: mergePerImage.value,
              modelTier: modelTier.value,
              pricesOnly: pricesOnly.value,
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
          } else {
            // 超长报价单智能找缝切片 + 第 1 段先提取精准列头，第 2..N 段双并发提取并带红线列头视觉锚定
            const seamTag = slicePlan.seamMode === "smart_seam" ? "智能找缝" : "重叠防漏";
            const anchorTag = slicePlan.hasHeaderAnchor ? "·列头锚定" : "";
            const sliceResults: Array<Record<string, unknown> | null> = new Array(slices.length).fill(null);
            let completedSlices = 0;

            const runOneSlice = async (sIdx: number, hintStr: string): Promise<Record<string, unknown>> => {
              let lastSliceErr: unknown = null;
              const maxAttempts = 3;
              for (let attempt = 1; attempt <= maxAttempts; attempt++) {
                progressItems.value[myIdx].message =
                  attempt === 1
                    ? `${seamTag}分 ${slices.length} 段${anchorTag}，并发提取 (${completedSlices}/${slices.length} 段已完成)...`
                    : `第 ${sIdx + 1}/${slices.length} 段遇网络或模型波动，正在自动重试 (${attempt}/${maxAttempts})...`;
                try {
                  const res = await extractSlice({
                    file: slices[sIdx],
                    sliceIndex: sIdx,
                    mode: mode.value,
                    forceDemo: forceDemo.value,
                    customHint: hintStr,
                    modelTier: modelTier.value,
                    pricesOnly: pricesOnly.value,
                  });
                  completedSlices++;
                  progressItems.value[myIdx].message = `${seamTag}分 ${slices.length} 段${anchorTag}，提取进度 (${completedSlices}/${slices.length})...`;
                  return res;
                } catch (err) {
                  lastSliceErr = err;
                  const status = axios.isAxiosError(err) ? err.response?.status : undefined;
                  if (status === 400 || status === 401 || status === 403 || status === 429) {
                    throw err;
                  }
                  if (attempt < maxAttempts) {
                    await sleep(1200 * attempt);
                  }
                }
              }
              throw lastSliceErr instanceof Error ? lastSliceErr : new Error(`第 ${sIdx + 1} 段识别失败`);
            };

            // 1. 先提取第 1 段以获得最准确的列头结构
            const baseHint = customHint.value.trim();
            const firstRes = await runOneSlice(0, baseHint);
            sliceResults[0] = firstRes;

            let inheritedHeaderHint = baseHint;
            const rawList = (firstRes.raw_sheets as Array<{ headers?: string[] }> | undefined) || [];
            const firstHeaders = rawList[0]?.headers;
            if (firstHeaders?.length) {
              const headerStr = firstHeaders.join(" | ");
              const contNote =
                `This slice continues the same quotation table. Keep the EXACT ${firstHeaders.length} columns in order: [${headerStr}]. ` +
                (slicePlan.hasHeaderAnchor
                  ? "The top strip above the red horizontal bar is ONLY a visual header reference—extract ONLY data rows below the red bar. "
                  : "") +
                "Keep '-' or empty placeholder slots for missing tiers and do NOT shift prices left or omit rightmost columns.";
              inheritedHeaderHint = inheritedHeaderHint ? `${inheritedHeaderHint}\n${contNote}` : contNote;
            }

            // 2. 第 2..N 段采用 2 并发加速提取
            let nextSliceIdx = 1;
            const sliceWorker = async () => {
              while (nextSliceIdx < slices.length) {
                const curS = nextSliceIdx++;
                sliceResults[curS] = await runOneSlice(curS, inheritedHeaderHint);
              }
            };
            await Promise.all(
              Array.from({ length: Math.min(2, slices.length - 1) }, () => sliceWorker())
            );

            progressItems.value[myIdx].message = `正在全局去重拼接 ${slices.length} 段并做异常自检...`;
            const committed = await commitSlicedQuotation({
              jobUid: createdJob.job_uid,
              itemIndex: myIdx,
              sourceFilename: qItem.file.name,
              fileSizeBytes: qItem.file.size,
              mode: mode.value,
              mergePerImage: mergePerImage.value,
              sliceResults: sliceResults.filter((x): x is Record<string, unknown> => x !== null),
            });
            if (!committed.quotation) {
              throw new Error("长图合并归档失败");
            }
            collected[myIdx] = committed.quotation;
            if (!committed.quotation.is_demo) {
              geminiConfigured.value = true;
              activeModel.value = committed.quotation.engine;
            }
            if (committed.quota) {
              quotaRemaining.value = committed.quota.remaining;
              quotaLimit.value = committed.quota.daily_limit;
            }
            const warnCnt = committed.quotation.merged_sheet.warnings?.length || 0;
            progressItems.value[myIdx].status = "completed";
            progressItems.value[myIdx].quoteTitle = committed.quotation.quote_title;
            progressItems.value[myIdx].message =
              warnCnt > 0
                ? `${seamTag} ${slices.length} 段完成 (${committed.quotation.merged_sheet.rows.length} 行 · ${warnCnt} 处自检提醒)`
                : `${seamTag} ${slices.length} 段完成 (${committed.quotation.merged_sheet.rows.length} 行 · 已存 B2)`;
          }

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
    const [jobs, records, stats] = await Promise.all([
      fetchBatchJobs(),
      fetchHistory(),
      fetchStorageStats().catch(() => null),
    ]);
    batchJobs.value = jobs;
    historyList.value = records;
    if (stats) storageStats.value = stats;
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
    const res = await deleteBatchJob(jobUid);
    batchJobs.value = batchJobs.value.filter((j) => j.job_uid !== jobUid);
    const freedInfo =
      res.deleted_files && res.deleted_files > 0
        ? `，已同步释放 ${res.deleted_files} 个云端文件 (${formatSize(res.freed_bytes || 0)})`
        : "";
    ElMessage.success(`批次记录已删除${freedInfo}`);
    if (res.storage_warning) {
      ElMessage.warning(`云端清理提醒：${res.storage_warning}`);
    }
    fetchStorageStats()
      .then((s) => (storageStats.value = s))
      .catch(() => {});
  } catch (err) {
    ElMessage.error(extractApiError(err));
  }
}

async function confirmClearAllRecords() {
  try {
    await ElMessageBox.confirm(
      "此操作将一次性删除全部批次任务、单张调用明细，并彻底物理删除 Backblaze B2 存储桶中的所有存档文件与历史版本（不可恢复），确认继续？",
      "一键清空全部记录并释放空间",
      {
        confirmButtonText: "确认全部清空",
        cancelButtonText: "取消",
        type: "warning",
      }
    );
  } catch {
    return;
  }

  clearingAll.value = true;
  try {
    const res = await clearAllRecords();
    batchJobs.value = [];
    historyList.value = [];
    ElMessage.success(
      `已清空 ${res.deleted_jobs} 个批次、${res.deleted_records} 条明细，并释放 ${res.deleted_files} 个云端文件版本（${formatSize(res.freed_bytes)}）`
    );
    if (res.storage_warning) {
      ElMessage.warning(`云端清理提醒：${res.storage_warning}`);
    }
    storageStats.value = await fetchStorageStats().catch(() => null);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    clearingAll.value = false;
  }
}

async function confirmPurgeB2() {
  try {
    await ElMessageBox.confirm(
      "将扫描并彻底删除 Backblaze B2 存储桶中的所有文件及历史隐藏版本（用于清理残留占用、清零 10GB 免费空间），保留文字明细列表，确认执行？",
      "一键释放 Backblaze B2 空间",
      {
        confirmButtonText: "立即释放空间",
        cancelButtonText: "取消",
        type: "warning",
      }
    );
  } catch {
    return;
  }

  purgingB2.value = true;
  try {
    const res = await purgeB2Storage();
    ElMessage.success(`Backblaze B2 已清理完成：删除 ${res.deleted_files} 个文件版本，释放 ${formatSize(res.freed_bytes)}`);
    await loadHistory();
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    purgingB2.value = false;
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

async function loadWorkspaceBootstrap() {
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
    geminiConfigured.value = Boolean(health.gemini_configured);
    b2Configured.value = Boolean(health.b2_configured);
    if (health.model) activeModel.value = health.model;
    quotaRemaining.value = quota.remaining;
    quotaLimit.value = quota.daily_limit;
  } catch {
    // 后端未启动或未授权时保持界面可用
  }

  if (activeTab.value === "history") {
    loadHistory();
  }
}

async function handleLogin() {
  if (!loginUsername.value.trim() || !loginPassword.value || loggingIn.value) return;
  loggingIn.value = true;
  loginError.value = "";
  try {
    const res = await loginWithPassword(loginUsername.value.trim(), loginPassword.value);
    authenticated.value = true;
    currentUsername.value = res.username;
    loginPassword.value = "";
    ElMessage.success(`欢迎回来，${res.username}`);
    await loadWorkspaceBootstrap();
  } catch (err) {
    loginError.value = extractApiError(err);
  } finally {
    loggingIn.value = false;
  }
}

function handleLogout() {
  clearStoredAuthToken();
  authenticated.value = false;
  currentUsername.value = "";
  quotations.value = [];
  batchJobs.value = [];
  historyList.value = [];
  storageStats.value = null;
  progressItems.value = [];
  clearQueue();
  localStorage.removeItem(DRAFT_STORAGE_KEY);
  ElMessage.info("已安全退出登录并清理本地草稿");
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
  { title: "批量拖入或粘贴报价单", desc: "支持一次选入多达 20 张长图报价单，超长图自动高精度重叠切片并自动去重拼接。" },
  { title: "忠实还原原表结构", desc: "A 列直接从原图型号列起步，不同成色区块自动保留原表分段表头并渲染 WPS 苹果绿样式。" },
  { title: "分片容错 + 自动存 B2", desc: "单段切片失败自动重试并跨模型兜底，成品单表 .xlsx 与整批 .zip 自动上传至 Backblaze B2。" },
  { title: "随时下载或一键释放空间", desc: "转换记录页支持下载、载入复改，也支持一键清空全部记录并物理删除 B2 历史版本释放额度。" },
];

const useCases = [
  { title: "1 图 = 1 完整报价单 Sheet", tag: "忠实还原", pillClass: "pill-emerald", desc: "不强插多余列，A 列直接对应原图首列，自动保留各区块原生表头且每行带型号便于筛选与 VLOOKUP。" },
  { title: "单份导出与批量打包 .zip", tag: "多图批处理", pillClass: "pill-sky", desc: "支持单份报价单独立下载 .xlsx，多张报价单时一键打包成平铺 .zip 压缩包。" },
  { title: "长图保宽清晰 + 300% 放大", tag: "长截图友好", pillClass: "pill-violet", desc: "高清切片配合左侧 300% 缩放对照窗，长截图里的密集小字与价格格一目了然。" },
  { title: "Backblaze B2 存档与一键清零", tag: "对象存储", pillClass: "pill-gold", desc: "自动存档 .xlsx / .zip 至 B2，并在转换记录页提供一键清空与 B2 历史版本物理删除，省心控空间。" },
  { title: "商家规则备注单独提取", tag: "纯净数据行", pillClass: "pill-coral", desc: "顶部时效与扣差说明单独展示在提示栏，不污染数据行，价格列保留纯数字便于直接公式计算。" },
  { title: "私有账号密码全站保护", tag: "安全鉴权", pillClass: "pill-sky", desc: "全站接口强制校验登录令牌，防止外部访客盗刷 AI 识别配额与对象存储空间。" },
];

const faqs = [
  {
    q: "为什么多张报价单改用「分片进度调度」？",
    a: "超长报价单识别往往需要 15~40 秒。改成单图高清分段 + 单段失败自动重试与跨模型兜底后，既规避 Vercel 60 秒超时，又能避免切缝截断多出错误机型。",
  },
  {
    q: "怎么释放 Backblaze B2 免费 10GB 存储空间？",
    a: "在「转换记录」页面点击单条记录的「删除并释放」，或直接点击右上角「一键清空全部记录 / 一键释放 B2 空间」，系统会彻底删除 B2 中的所有文件及历史隐藏版本。",
  },
  {
    q: "合并成一个 Sheet 后还能查看原来的分块吗？",
    a: "可以。我们在编辑区右上角保留了「合为单表 / 拆分子表」开关，切换即可看原始拆分的每个区块子表，不会重新消耗 API 次数。",
  },
  {
    q: "报价单里的区间价或 +50 加减价会被当成公式吗？",
    a: "不会。纯数字价格（如 3400、¥2,199.00）会转为真正的 Excel 整数格式方便核对与求和；带有连字符区间（如 3200-3300）或显式加号（+50）的文本会自动保持文本原样。",
  },
];

onMounted(async () => {
  setUnauthorizedHandler(() => {
    if (authRequired.value) {
      authenticated.value = false;
      currentUsername.value = "";
      ElMessage.warning("登录状态已失效，请重新登录");
    }
  });

  try {
    const status = await fetchAuthStatus();
    authRequired.value = status.auth_required;
    authConfigured.value = status.auth_configured;
    authenticated.value = status.authenticated;
    currentUsername.value = status.username || "";
  } catch {
    authRequired.value = false;
    authenticated.value = true;
  } finally {
    authChecking.value = false;
  }

  if (!authRequired.value || authenticated.value) {
    await loadWorkspaceBootstrap();
  }
});
</script>
