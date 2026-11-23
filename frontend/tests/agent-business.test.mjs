import assert from "node:assert/strict";
import test from "node:test";
import { businessLabel, caseBusiness } from "../src/agentBusiness.ts";

test("case analysts follow their assigned case even when a saved business label is stale", () => {
  for (const [caseId, label] of [["G03", "地理态势"], ["T12", "科技情报"], ["B11", "战场情报"]]) {
    const agent = {
      role_contract: { stage: "analyze", case_id: caseId, shared_cases: ["B01", "T01"] },
      parameters: { case_scope: "B02" },
      management_profile: { business: "general" },
    };
    assert.equal(caseBusiness(agent).caseId, caseId);
    assert.equal(businessLabel(agent), label);
    assert.equal(businessLabel({ parameters: { case_scope: caseId } }), label);
  }
});

test("shared case roles are general across all the cases they support", () => {
  for (const stage of ["prepare", "review"]) {
    const agent = { case_role: stage, role_contract: { shared_cases: ["G03", "T12"] } };
    assert.equal(businessLabel(agent), "通用");
    assert.equal(caseBusiness(agent).caseId, undefined);
  }
});

test("other agents keep their configured business category and legacy values remain readable", () => {
  for (const [business, label] of [
    ["general", "通用"], ["science", "科技情报"], ["geographic", "地理态势"],
    ["battlefield", "战场情报"], ["historical", "战场情报"], ["public_sources", "通用"],
  ]) {
    assert.equal(businessLabel({ management_profile: { business } }), label);
  }
  assert.equal(businessLabel({ name: "G03", parameters: { case_scope: "TBD" } }), "通用");
});
