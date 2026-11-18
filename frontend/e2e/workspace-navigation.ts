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
  if (name === "工作台" || name === "任务中心") {
    await button("工作台·任务中心").click();
    await tab(name).click();
  } else if (["智能体资产", "工作流", "工具箱", "扩展接入"].includes(name)) {
    await button("专家·技能·连接器").click();
    await tab(
      name === "工具箱" ? "技能" : name === "扩展接入" ? "连接器" : "专家",
    ).click();
    if (name === "工作流")
      await page
        .locator(".hub-section-tabs")
        .getByRole("button", { name: "工作流", exact: true })
        .click();
    await page.locator(".hub-manage").click();
  } else if (name === "我的资料" || name === "知识库") {
    await button("知识库·资料库·发现").click();
    await tab(name === "我的资料" ? "资料库" : "知识库").click();
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
