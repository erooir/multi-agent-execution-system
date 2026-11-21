import { expect, type Page } from "@playwright/test";

export const group = (page: Page, id: string) =>
  page.locator(`.wb-group-row[data-project-id="${id}"]`);
export const currentGroup = (page: Page) =>
  page.locator('.wb-group-row[aria-current="true"]');

export async function workspacePage(page: Page, name: string) {
  const nav = page.getByRole("navigation", { name: "主导航" });
  const button = (name: string) =>
    nav.getByRole("button", { name, exact: true });
  const tab = (name: string) =>
    page.locator(".wb-topbar").getByRole("button", { name, exact: true });
  if (["工作台", "任务中心", "智能体监测", "工作流", "资源计量"].includes(name)) {
    await button("工作台·任务中心").click();
    await tab(name === "工作流" ? "流程与经验" : name).click();
  } else if (["智能体", "工具箱", "扩展接入", "连接器"].includes(name)) {
    await button("智能体·技能·连接器").click();
    await tab(
      name === "连接器" ? name : ["工具箱", "扩展接入"].includes(name) ? "技能" : "智能体",
    ).click();
    if (name === "扩展接入") await page.getByRole("button", { name: "扩展管理" }).click();
    if (name === "工具箱") await page.getByRole("button", { name: "工具箱", exact: true }).click();
  } else if (name === "我的资料" || name === "知识库") {
    await button("知识库").click();
    await tab(name === "我的资料" ? "资料与版本" : "检索与记忆").click();
  } else if (name === "设置") {
    await page.locator(".wb-user-chip").click();
    await page.getByRole("button", { name: "设置", exact: true }).click();
    await page.getByRole("button", { name: "打开完整设置页" }).click();
    await expect(
      page.getByRole("dialog", { name: "设置", exact: true }),
    ).toHaveCount(0);
  } else {
    await button(name).click();
  }
}
