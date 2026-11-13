import axios from "axios";

export const api = axios.create({ baseURL: import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1" });
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("bharatbench_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export async function getOverview() { return (await api.get("/analytics/overview")).data; }
export async function getDatasets() { return (await api.get("/datasets?page_size=100")).data; }
export async function getExperiments() { return (await api.get("/experiments?page_size=100")).data; }
export async function getModelAccuracy() { return (await api.get("/analytics/models")).data; }
export async function getLanguageAccuracy() { return (await api.get("/analytics/languages")).data; }
export async function getBiasAnalysis() { return (await api.get("/analytics/bias")).data; }
export async function createExperiment(payload: unknown) { return (await api.post("/experiments", payload)).data; }
export async function createEvaluation(payload: unknown) { return (await api.post("/evaluations", payload)).data; }
export async function getResults(experimentId: string) { return (await api.get(`/experiments/${experimentId}/results?page_size=100`)).data; }
export async function validateDataset(file: File) { const form = new FormData(); form.append("file", file); return (await api.post("/datasets/validate", form)).data; }
export async function importDataset(file: File, name: string) { const form = new FormData(); form.append("file", file); form.append("name", name); return (await api.post("/datasets/import", form)).data; }
export async function login(username: string, password: string) {
  const result = await api.post("/auth/login", { username, password });
  localStorage.setItem("bharatbench_token", result.data.access_token);
  return result.data;
}
