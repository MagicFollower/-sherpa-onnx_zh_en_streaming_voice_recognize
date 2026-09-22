import { createApp } from 'vue'
import App from './App.vue'
import './style.css'
// 仅加载同源静态资源；不注册远程字体、遥测、Service Worker或云语音SDK。
createApp(App).mount('#app')
