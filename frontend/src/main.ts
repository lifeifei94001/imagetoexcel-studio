import { createApp } from "vue";
import ElementPlus from "element-plus";
import zhCn from "element-plus/es/locale/lang/zh-cn";
import "element-plus/dist/index.css";
import "element-plus/theme-chalk/dark/css-vars.css";
import { autoAnimatePlugin } from "@formkit/auto-animate/vue";

import App from "./App.vue";
import "./styles/glass.css"; // 必须放最后一行，彻底穿透覆盖 Element Plus 默认白底

const app = createApp(App);
app.use(ElementPlus, { locale: zhCn });
app.use(autoAnimatePlugin);
app.mount("#app");
