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
        <span class="gem-pill" :class="geminiConfigured ? 'pill-emerald' : 'pill-gold'">
          {{ geminiConfigured ? activeModel : "演示模式" }}
        </span>
        <span v-if="b2Configured" class="gem-pill pill-sky">B2 云存档</span>

        <el-select v-model="theme" size="small" style="width: 112px" aria-label="切换主题">
          <el-option label="🌸 霞光" value="aurora" />
          <el-option label="🌌 星云" value="nebula" />
          <el-option label="🏛️ 杂志" value="editorial" />
        </el-select>

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
        <section class="hero-section" style="padding: 16px 0 18px">
          <h1 class="hero-title" style="font-size: clamp(1.45rem, 2.6vw, 2rem); margin-bottom: 6px">
            图片智能转 Excel 工作台
          </h1>
          <p class="hero-subtitle" style="margin-bottom: 14px">
            支持手机/平板超长报价单、财务报表、发票明细与各类办公表格，智能找缝切片并自动归档至 Backblaze B2。
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

        <!-- 多设备并发运行提示横幅（检测到另一台电脑正在跑任务时友好提醒） -->
        <el-alert
          v-if="otherActiveJobs.length > 0 && !extracting"
          type="warning"
          show-icon
          :closable="false"
          style="margin-bottom: 16px; border-radius: var(--glass-radius-sm)"
        >
          <template #title>
            <div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap">
              <span>
                检测到另一台设备正在处理「{{ otherActiveJobs[0].title }}」（当前进度
                <strong>{{ otherActiveJobs[0].completed_items }}/{{ otherActiveJobs[0].total_items }}</strong>，已运行
                {{ formatElapsed(otherActiveJobs[0].elapsed_seconds) }}），为避免触发 API 并发限流，建议稍等片刻再开始。
              </span>
              <el-button size="small" plain type="warning" @click="checkOtherActiveJobs">刷新状态</el-button>
            </div>
          </template>
        </el-alert>

        <!-- 双栏队列式上传工作台 -->
        <section class="glass-strong uploader-shell">
          <div class="uploader-grid">
            <!-- 左栏：拖拽 / 粘贴 / 浏览 -->
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
                拖拽表格图片到此处、点击浏览，或 <kbd>Ctrl+V</kbd> 粘贴截图
              </div>
              <div class="glass-muted" style="font-size: 13px">
                支持 JPG, PNG, WEBP, GIF, HEIC · 单张上限 10MB · 单批最多 20 张长图
              </div>
              <div style="display: flex; gap: 10px; justify-content: center; margin-top: 6px" @click.stop>
                <el-button type="primary" @click="fileInputRef?.click()">选择图片文件</el-button>
              </div>
            </div>

            <!-- 右栏：待转换队列 + 核心参数 + 自定义额度 + 转换按钮 -->
            <div class="queue-panel">
              <div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px">
                  <span style="font-weight: 700; font-size: 14px">
                    待处理队列 (<span class="num-tabular">{{ queue.length }}</span>/20)
                  </span>
                  <span v-if="!geminiConfigured" class="gem-pill pill-gold">未配置 Key · 演示模式</span>
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
                <!-- 高频核心参数直出 -->
                <div
                  class="glass"
                  style="padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; border-radius: var(--glass-radius-sm)"
                >
                  <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 6px">
                    <span style="font-size: 12.5px; font-weight: 600">场景识别：</span>
                    <el-radio-group v-model="docType" size="small">
                      <el-radio value="auto">✨ 智能自动</el-radio>
                      <el-radio value="quotation">📱 报价单</el-radio>
                      <el-radio value="general">📊 通用表</el-radio>
                    </el-radio-group>
                  </div>

                  <div
                    v-if="docType !== 'general'"
                    style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 6px"
                  >
                    <span style="font-size: 12.5px; font-weight: 600">右侧备注列：</span>
                    <el-switch
                      v-model="pricesOnly"
                      active-text="仅提机型+价格"
                      inactive-text="完整保留"
                    />
                  </div>
                </div>

                <!-- 低频高级选项折叠区 -->
                <div
                  class="glass"
                  style="padding: 8px 12px; border-radius: var(--glass-radius-sm)"
                >
                  <button
                    type="button"
                    style="
                      width: 100%;
                      display: flex;
                      align-items: center;
                      justify-content: space-between;
                      background: transparent;
                      border: none;
                      padding: 2px 0;
                      cursor: pointer;
                      color: var(--glass-text);
                      font-size: 12.5px;
                      font-weight: 600;
                    "
                    @click="showAdvancedOptions = !showAdvancedOptions"
                  >
                    <span>高级选项</span>
                    <span class="glass-muted" style="font-weight: 400; font-size: 12px">
                      {{ modelTier === "fast" ? "Flash Lite" : "Hydrogen" }} ·
                      {{ mergePerImage ? "单图合表" : "保留子表" }} ·
                      {{ showAdvancedOptions ? "收起 ▲" : "展开 ▼" }}
                    </span>
                  </button>

                  <div
                    v-if="showAdvancedOptions"
                    style="display: flex; flex-direction: column; gap: 9px; margin-top: 10px; padding-top: 9px; border-top: 1px solid var(--glass-border)"
                  >
                    <el-input
                      v-model="customHint"
                      size="small"
                      clearable
                      placeholder="可选提示词（如：保留前导零、备注放提示栏）"
                    />
                    <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 6px">
                      <span style="font-size: 12px; font-weight: 600">单图多表处理：</span>
                      <el-switch
                        v-model="mergePerImage"
                        active-text="合为1个Sheet"
                        inactive-text="保留多子表"
                      />
                    </div>
                    <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 6px">
                      <span style="font-size: 12px; font-weight: 600">识别模型档位：</span>
                      <el-radio-group v-model="modelTier" size="small">
                        <el-radio value="fast">极速稳定</el-radio>
                        <el-radio value="accurate">旗舰精读</el-radio>
                      </el-radio-group>
                    </div>
                    <div style="display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 6px">
                      <span style="font-size: 12px; font-weight: 600">多图默认打包：</span>
                      <el-radio-group v-model="outputMode" size="small">
                        <el-radio value="per_image">一图一表 (.zip)</el-radio>
                        <el-radio value="all_in_one">合并多 Sheet</el-radio>
                      </el-radio-group>
                    </div>
                  </div>
                </div>

                <!-- 自定义每日额度条 -->
                <div
                  class="gem-pill pill-sky"
                  style="justify-content: space-between; padding: 6px 10px 6px 12px; border-radius: var(--glass-radius-sm)"
                >
                  <span style="font-size: 12.5px">今日剩余免费额度</span>
                  <div style="display: flex; align-items: center; gap: 8px">
                    <span class="num-tabular" style="font-weight: 700">
                      {{ quotaUnlimited ? `不限次（已用 ${quotaUsed}）` : `${quotaRemaining} / ${quotaLimit} 次` }}
                    </span>
                    <el-button size="small" text type="primary" style="padding: 2px 6px; height: auto" @click="openQuotaDialog">
                      设置
                    </el-button>
                  </div>
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
                    {{ extracting ? `正在后台分片处理 (${doneCount}/${progressItems.length})...` : "开始提取并归档" }}
                  </el-button>
                </div>
              </div>
            </div>
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
      </template>

      <!-- ==================== 使用指南视图 ==================== -->
      <section v-else-if="activeTab === 'guide'" class="glass-strong" style="padding: 24px">
        <h2 class="section-heading">4 步高效完成图片转 Excel</h2>
        <p class="section-sub">兼容手机/平板超长报价单与财务/发票/库存等通用表格，自动切片识别并归档至 Backblaze B2。</p>
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
                  <span class="gem-pill" :class="job.status === 'completed' ? 'pill-emerald' : job.status === 'running' ? 'pill-sky' : 'pill-gold'">
                    {{ job.completed_items }}/{{ job.total_items }} 份 · {{ formatJobStatus(job.status) }}
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
                    v-if="hasJobZipDownload(job) || (job.items.length === 1 && job.items[0]?.xlsx_b2_key)"
                    size="small"
                    type="primary"
                    :loading="downloadingKey === job.job_uid"
                    @click="
                      downloadStoredArtifact(
                        job.job_uid,
                        undefined,
                        hasJobZipDownload(job)
                          ? job.zip_filename || `${job.title}_打包.zip`
                          : job.items[0]?.xlsx_filename || '表格.xlsx'
                      )
                    "
                  >
                    {{
                      hasJobZipDownload(job)
                        ? `一键打包下载 (.zip · ${job.completed_xlsx_count || job.completed_items}份)`
                        : "下载表格 (.xlsx)"
                    }}
                  </el-button>
                  <el-button size="small" type="danger" text @click="removeBatchJob(job.job_uid)">删除并释放</el-button>
                </div>
              </div>

              <!-- 批次内每张表格明细 -->
              <div v-if="job.items.length > 0" style="display: grid; grid-template-columns: repeat(auto-fit, minmax(270px, 1fr)); gap: 8px">
                <div v-for="item in job.items" :key="item.id" class="queue-item">
                  <div style="min-width: 0; flex: 1">
                    <div style="font-weight: 600; font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap">
                      {{ item.quote_title || item.source_filename }}
                    </div>
                    <div
                      class="glass-muted num-tabular"
                      style="font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis"
                    >
                      {{ item.row_count }}×{{ item.col_count }} · {{ item.engine }}
                    </div>
                  </div>
                  <el-button
                    v-if="item.status === 'completed' && item.xlsx_b2_key"
                    size="small"
                    plain
                    style="flex-shrink: 0"
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

    <!-- 自定义每日额度设置弹窗 -->
    <el-dialog
      v-model="quotaDialogVisible"
      title="自定义每日免费额度"
      width="420px"
      align-center
      destroy-on-close
    >
      <div style="display: flex; flex-direction: column; gap: 16px">
        <div
          class="glass"
          style="padding: 10px 14px; display: flex; align-items: center; justify-content: space-between; border-radius: var(--glass-radius-sm)"
        >
          <span style="font-size: 13px">今日已使用次数</span>
          <div style="display: flex; align-items: center; gap: 10px">
            <span class="num-tabular" style="font-weight: 700; font-size: 15px">{{ quotaUsed }} 次</span>
            <el-button
              size="small"
              type="warning"
              plain
              :disabled="quotaUsed === 0 || savingQuota"
              @click="handleResetQuotaUsed"
            >
              重置今日已用
            </el-button>
          </div>
        </div>

        <div style="display: flex; align-items: center; justify-content: space-between">
          <div>
            <div style="font-weight: 600; font-size: 13.5px">不限次模式</div>
            <div class="glass-muted" style="font-size: 12px">开启后关闭每日上限校验，适合大批量集中处理</div>
          </div>
          <el-switch v-model="quotaFormUnlimited" />
        </div>

        <div v-if="!quotaFormUnlimited" style="display: flex; flex-direction: column; gap: 10px">
          <div style="display: flex; align-items: center; justify-content: space-between">
            <span style="font-weight: 600; font-size: 13.5px">每日上限次数</span>
            <el-input-number
              v-model="quotaFormLimit"
              :min="1"
              :max="100000"
              :step="50"
              size="default"
              style="width: 170px"
            />
          </div>
          <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap">
            <span class="glass-muted" style="font-size: 12px">快捷设定：</span>
            <el-button
              v-for="preset in [50, 200, 500, 1000]"
              :key="preset"
              size="small"
              :type="quotaFormLimit === preset ? 'primary' : 'default'"
              plain
              @click="quotaFormLimit = preset"
            >
              {{ preset }} 次
            </el-button>
          </div>
        </div>

        <div class="glass-muted" style="font-size: 12px; line-height: 1.5">
          保存后将写入数据库，立即对今日及后续每天持续生效。
        </div>
      </div>

      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px">
          <el-button @click="quotaDialogVisible = false">取消</el-button>
          <el-button type="primary" :loading="savingQuota" @click="handleSaveQuotaConfig">
            保存并持久生效
          </el-button>
        </div>
      </template>
    </el-dialog>
    </template>
  </div>
</template>

<script setup lang="ts">
import axios from "axios";
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
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
  fetchActiveJobs,
  fetchAuthStatus,
  fetchBatchJobs,
  fetchHealth,
  fetchHistory,
  fetchQuota,
  fetchStorageStats,
  finalizeBatchJob,
  getClientSessionId,
  loadJobIntoEditor,
  loginWithPassword,
  processBatchJobItem,
  purgeB2Storage,
  sendJobHeartbeat,
  setUnauthorizedHandler,
  updateQuotaConfig,
  type ActiveJobSummary,
  type BatchJob,
  type ConversionRecord,
  type DocScenarioType,
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
const docType = ref<DocScenarioType>("auto");
const outputMode = ref<"all_in_one" | "per_image">("per_image");
const mergePerImage = ref(true);
const pricesOnly = ref(false);
const modelTier = ref<"fast" | "accurate">("fast");
const forceDemo = ref(false);

watch(docType, (val) => {
  if (val === "general") {
    pricesOnly.value = false;
    mergePerImage.value = false;
  } else if (val === "quotation") {
    mergePerImage.value = true;
  }
});
const customHint = ref("");
const showAdvancedOptions = ref(false);
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
const quotaUsed = ref(0);
const quotaUnlimited = ref(false);
const quotaDialogVisible = ref(false);
const quotaFormLimit = ref(50);
const quotaFormUnlimited = ref(false);
const savingQuota = ref(false);

// 多设备并发运行检测 + 防误关拦截
const clientSessionId = getClientSessionId();
const otherActiveJobs = ref<ActiveJobSummary[]>([]);
let activeJobsPollTimer: number | null = null;
let heartbeatTimer: number | null = null;

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

useEventListener(window, "beforeunload", (e: BeforeUnloadEvent) => {
  if (extracting.value) {
    e.preventDefault();
    e.returnValue = "批量图片转表格任务正在运行中，刷新或关闭页面将中断未完成的图片，确定离开吗？";
  }
});

useEventListener(document, "visibilitychange", () => {
  if (document.visibilityState === "visible" && (!authRequired.value || authenticated.value)) {
    checkOtherActiveJobs();
  }
});

async function checkOtherActiveJobs() {
  if (authRequired.value && !authenticated.value) return;
  try {
    const res = await fetchActiveJobs(clientSessionId);
    otherActiveJobs.value = res.other_active_jobs || [];
  } catch {
    // 静默忽略轮询偶发波动
  }
}

function formatElapsed(sec: number): string {
  const s = Math.max(0, Math.floor(sec || 0));
  if (s < 60) return `${s}秒`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return r > 0 ? `${m}分${r}秒` : `${m}分钟`;
}

function formatJobStatus(status: string): string {
  if (status === "completed") return "已完成";
  if (status === "running") return "运行中";
  if (status === "partial_failed") return "部分完成";
  if (status === "failed") return "失败";
  return status;
}

function hasJobZipDownload(job: BatchJob): boolean {
  if (job.can_download_zip || job.zip_b2_key) return true;
  const xlsxCnt = job.items?.filter((it) => it.status === "completed" && Boolean(it.xlsx_b2_key)).length || 0;
  return xlsxCnt >= 2;
}

function stopHeartbeat() {
  if (heartbeatTimer !== null) {
    window.clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}

function startActiveJobsPolling() {
  if (activeJobsPollTimer !== null) return;
  checkOtherActiveJobs();
  activeJobsPollTimer = window.setInterval(() => {
    if (document.visibilityState === "visible" && !extracting.value) {
      checkOtherActiveJobs();
    }
  }, 15000);
}

function stopActiveJobsPolling() {
  if (activeJobsPollTimer !== null) {
    window.clearInterval(activeJobsPollTimer);
    activeJobsPollTimer = null;
  }
}

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

  // 开跑前实时检测另一台设备是否正在执行任务（软提醒 + 二次确认，防撞车限流）
  try {
    const activeInfo = await fetchActiveJobs(clientSessionId);
    otherActiveJobs.value = activeInfo.other_active_jobs || [];
    if (activeInfo.has_other_active && activeInfo.other_active_jobs.length > 0) {
      const running = activeInfo.other_active_jobs[0];
      try {
        await ElMessageBox.confirm(
          `检测到另一台设备正在处理「${running.title}」（当前进度 ${running.completed_items}/${running.total_items}，已运行 ${formatElapsed(running.elapsed_seconds)}）。同时运行容易触发模型并发限流，建议稍等片刻再开始。`,
          "另一台设备正在运行中",
          {
            confirmButtonText: "仍然立即开始",
            cancelButtonText: "先等一下",
            type: "warning",
          }
        );
      } catch {
        return;
      }
    }
  } catch {
    // 忽略检测网络抖动，不阻断正常使用
  }

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
        : `批量表格_${snapshot.length}份`;

    const createdJob = await createBatchJob({
      title: batchTitle,
      mode: mode.value,
      outputMode: outputMode.value,
      mergePerImage: mergePerImage.value,
      totalItems: snapshot.length,
      clientId: clientSessionId,
    });
    lastBatchJobUid.value = createdJob.job_uid;

    // 启动每 20 秒心跳，让其他电脑实时看到本机运行进度，断线 2 分钟自动释放
    stopHeartbeat();
    heartbeatTimer = window.setInterval(() => {
      sendJobHeartbeat(createdJob.job_uid, clientSessionId).catch(() => {});
    }, 20000);

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
              pricesOnly: docType.value === "general" ? false : pricesOnly.value,
              docType: docType.value,
            });
            collected[myIdx] = res.quotation;
            if (res.quotation.suggest_split_sheets && snapshot.length === 1) {
              mergePerImage.value = false;
            }
            if (!res.quotation.is_demo) {
              geminiConfigured.value = true;
              activeModel.value = res.quotation.engine;
            }
            quotaRemaining.value = res.quota.remaining;
            quotaLimit.value = res.quota.daily_limit;
            quotaUsed.value = res.quota.used_count;
            quotaUnlimited.value = Boolean(res.quota.unlimited || res.quota.daily_limit <= 0);

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
                    pricesOnly: docType.value === "general" ? false : pricesOnly.value,
                    docType: docType.value,
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
                `This slice continues the same table. Keep the EXACT ${firstHeaders.length} columns in order: [${headerStr}]. ` +
                (slicePlan.hasHeaderAnchor
                  ? "The top strip above the red horizontal bar is ONLY a visual header reference—extract ONLY data rows below the red bar. "
                  : "") +
                "Keep '-' or empty placeholder slots for blank cells and do NOT shift columns left or omit rightmost columns.";
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
              docType: docType.value,
              sliceResults: sliceResults.filter((x): x is Record<string, unknown> => x !== null),
            });
            if (!committed.quotation) {
              throw new Error("长图合并归档失败");
            }
            collected[myIdx] = committed.quotation;
            if (committed.quotation.suggest_split_sheets && snapshot.length === 1) {
              mergePerImage.value = false;
            }
            if (!committed.quotation.is_demo) {
              geminiConfigured.value = true;
              activeModel.value = committed.quotation.engine;
            }
            if (committed.quota) {
              quotaRemaining.value = committed.quota.remaining;
              quotaLimit.value = committed.quota.daily_limit;
              quotaUsed.value = committed.quota.used_count;
              quotaUnlimited.value = Boolean(committed.quota.unlimited || committed.quota.daily_limit <= 0);
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

    const finalizeWithRetry = async () => {
      let lastFinErr: unknown = null;
      for (let attempt = 1; attempt <= 2; attempt++) {
        try {
          await finalizeBatchJob({
            jobUid: createdJob.job_uid,
            quotations: [],
            mergePerImage: mergePerImage.value,
            outputMode: outputMode.value,
          });
          return;
        } catch (err) {
          lastFinErr = err;
          if (attempt < 2) {
            await sleep(1000);
          }
        }
      }
      throw lastFinErr;
    };

    if (validQuotations.length > 0) {
      // 轻量级 finalize：不再上传几 MB 整批 JSON（避免撞 Vercel 4.5MB 413），由后端直接从 B2 现存单表 .xlsx 打包 .zip！
      await finalizeWithRetry();
      ElMessage.success(
        validQuotations.length === snapshot.length
          ? `全部 ${validQuotations.length} 份表格已提取并打包归档！`
          : `已完成 ${validQuotations.length}/${snapshot.length} 份（已自动归档已完成文件）`
      );
      await nextTick();
      editorAnchorRef.value?.scrollIntoView({ behavior: "smooth", block: "start" });
    } else {
      await finalizeWithRetry();
      ElMessage.error("本批次图片均未能提取成功，请检查进度栏错误原因");
    }
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    stopHeartbeat();
    extracting.value = false;
    checkOtherActiveJobs();
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
    quotaUsed.value = quota.used_count;
    quotaUnlimited.value = Boolean(quota.unlimited || quota.daily_limit <= 0);
  } catch {
    // 后端未启动或未授权时保持界面可用
  }

  if (activeTab.value === "history") {
    loadHistory();
  }
}

function openQuotaDialog() {
  quotaFormUnlimited.value = quotaUnlimited.value;
  quotaFormLimit.value = quotaLimit.value > 0 ? quotaLimit.value : 200;
  quotaDialogVisible.value = true;
}

async function handleSaveQuotaConfig() {
  savingQuota.value = true;
  try {
    const updated = await updateQuotaConfig({
      unlimited: quotaFormUnlimited.value,
      dailyLimit: quotaFormUnlimited.value ? 0 : Math.max(1, Math.round(quotaFormLimit.value || 50)),
    });
    quotaRemaining.value = updated.remaining;
    quotaLimit.value = updated.daily_limit;
    quotaUsed.value = updated.used_count;
    quotaUnlimited.value = Boolean(updated.unlimited || updated.daily_limit <= 0);
    quotaDialogVisible.value = false;
    ElMessage.success(quotaUnlimited.value ? "已切换为不限次模式并持久保存" : `每日额度已更新为 ${quotaLimit.value} 次并持久保存`);
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    savingQuota.value = false;
  }
}

async function handleResetQuotaUsed() {
  savingQuota.value = true;
  try {
    const updated = await updateQuotaConfig({ resetUsed: true });
    quotaRemaining.value = updated.remaining;
    quotaLimit.value = updated.daily_limit;
    quotaUsed.value = updated.used_count;
    quotaUnlimited.value = Boolean(updated.unlimited || updated.daily_limit <= 0);
    ElMessage.success("今日已用次数已重置为 0");
  } catch (err) {
    ElMessage.error(extractApiError(err));
  } finally {
    savingQuota.value = false;
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
    startActiveJobsPolling();
  } catch (err) {
    loginError.value = extractApiError(err);
  } finally {
    loggingIn.value = false;
  }
}

async function handleLogout() {
  if (extracting.value) {
    try {
      await ElMessageBox.confirm("当前仍有识别任务正在运行，退出登录将中断剩余图片处理，确定退出吗？", "任务运行中", {
        confirmButtonText: "强制退出",
        cancelButtonText: "取消",
        type: "warning",
      });
    } catch {
      return;
    }
  }
  stopHeartbeat();
  stopActiveJobsPolling();
  otherActiveJobs.value = [];
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
  { title: "拖入或 Ctrl+V 粘贴图片", desc: "支持一次选入最多 20 张图片，超长报价单与长表格自动 1:1 沿横线智能找缝切片并附带单行表头视觉锚定。" },
  { title: "智能区分报价单与通用表", desc: "默认「智能自动」模式：手机/平板报价单自动展开合并格并套用苹果绿样式；财务/发票/库存表自动保护合计行与货币千分位，套用商务灰蓝风格。" },
  { title: "分片并发容错 + 自动存 B2", desc: "长图每段独立计时与最多 3 轮自动重试，无总时长截断；成品单表 .xlsx 与整批 .zip 自动归档至 Backblaze B2。" },
  { title: "自由控额度与一键清空间", desc: "工作台可直接自定义每日额度或切换不限次；转换记录页支持一键清空全部记录并物理清理 B2 历史版本。" },
];

const faqs = [
  {
    q: "超长报价单识别超过 1 分钟会超时跳过吗？",
    a: "不会。超长图会被自动切分为多段（最多 30 段），每一小段独立请求计时并带最多 3 轮前端重试与跨模型切换兜底，整张图无论耗时 60 秒还是 120 秒都会完整拼合输出。",
  },
  {
    q: "如何自定义今日免费额度或关闭次数限制？",
    a: "在工作台右侧「今日剩余免费额度」条上点击「设置」，可自由输入每日上限、一键切换「不限次模式」或「重置今日已用次数」，保存后会持久化到数据库并持续对后续日期生效。",
  },
  {
    q: "财务报表、发票明细、库存单等非报价单图片适用吗？",
    a: "适用。保持默认「智能自动」或手动切到「通用表」模式，系统会自动跳过报价单专属规则，完整保留合计/小计行、货币符号与千分位格式（如 ¥#,##0.00），遇到一图多结构表还会自动拆成独立 Sheet。",
  },
  {
    q: "怎么释放 Backblaze B2 免费 10GB 存储空间？",
    a: "在「转换记录」页面点击单条记录的「删除并释放」，或点击右上角「一键清空全部记录 / 一键释放 B2 空间」，即可同步物理删除 B2 中的所有文件及历史隐藏版本。",
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
    startActiveJobsPolling();
  }
});

onBeforeUnmount(() => {
  stopHeartbeat();
  stopActiveJobsPolling();
});
</script>
