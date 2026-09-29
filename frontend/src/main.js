// 页面状态由 Vue 管理，上传原文只按文本显示，不使用 v-html 渲染不可信内容。
import { createApp } from "vue";
import App from "./App.vue";
import "./style.css";
createApp(App).mount("#app");
