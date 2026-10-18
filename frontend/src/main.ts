import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { createRouter, createWebHistory } from 'vue-router'
import App from './App.vue'
import './style.css'

const routes = [
  { path: '/assistant', name: 'assistant', component: () => import('./views/AssistantView.vue'), meta: { title: '助手' } },
  { path: '/', name: 'home', component: () => import('./views/HomeView.vue'), meta: { title: '总览' } },
  { path: '/jobs', name: 'jobs', component: () => import('./views/JobsView.vue'), meta: { title: '岗位库' } },
  { path: '/jobs/:id', name: 'job-detail', component: () => import('./views/JobDetailView.vue'), meta: { title: '岗位详情' } },
  { path: '/resumes', name: 'resumes', component: () => import('./views/ResumesView.vue'), meta: { title: '简历库' } },
  { path: '/resumes/generated/:id', name: 'resume-editor', component: () => import('./views/ResumeEditorView.vue'), meta: { title: '简历编辑' } },
  { path: '/resumes/imported', name: 'imported-list', component: () => import('./views/ImportedResumesView.vue'), meta: { title: '导入简历' } },
  { path: '/resumes/imported/:id', name: 'imported-resume', component: () => import('./views/ImportedResumeView.vue'), meta: { title: '导入简历' } },
  { path: '/evidence', name: 'evidence', component: () => import('./views/EvidenceView.vue'), meta: { title: '证据账本' } },
  { path: '/evaluations', name: 'evaluations', component: () => import('./views/EvaluationsView.vue'), meta: { title: '简历评价' } },
  { path: '/evaluations/:id', name: 'evaluation-detail', component: () => import('./views/EvaluationDetailView.vue'), meta: { title: '评价报告' } },
  { path: '/projects', name: 'projects', component: () => import('./views/ProjectsView.vue'), meta: { title: '项目导学' } },
  { path: '/projects/:id', name: 'project-detail', component: () => import('./views/ProjectDetailView.vue'), meta: { title: '项目详情' } },
  { path: '/interviews', name: 'interviews', component: () => import('./views/InterviewsView.vue'), meta: { title: '模拟面试' } },
  { path: '/interviews/:id', name: 'interview', component: () => import('./views/InterviewView.vue'), meta: { title: '模拟面试' } },
  { path: '/interviews/:id/report', name: 'interview-report', component: () => import('./views/InterviewReportView.vue'), meta: { title: '面试报告' } },
  { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue'), meta: { title: '设置' } },
  { path: '/runs/:id', name: 'run', component: () => import('./views/RunDetailView.vue'), meta: { title: 'Run 详情' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

createApp(App).use(createPinia()).use(router).mount('#app')
