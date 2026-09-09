#!/usr/bin/env python3
"""Emit a throwaway n8n workflow that creates the four DocuSign templates.

Why a workflow and not a script: the sandbox this repository is developed in
cannot reach the DocuSign API, and the OAuth token lives inside n8n. Borrowing
n8n's credential for one manual run is cheaper and safer than exporting a
token to somewhere it can leak.

The run does two things in order:

  1. GET /oauth/userinfo  -> the API account id and the account's base URI.
     Hard-coding either is a common way to end up with a 401 that looks like a
     scope problem, so it is read rather than assumed.
  2. POST /templates x4   -> one template per role, each with the contract PDF
     inline and three anchored tabs.

The output of the run is four template ids plus the account id, which is
exactly the set of REPLACE_ME values in every workflow's Config node.

Delete this workflow once the ids are recorded. It has no place in the running
system and nothing references it.

Run:  python3 docusign/build_template_workflow.py
Out:  n8n/workflows/TEMP_docusign_templates.json
"""

import base64
import json
import os
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONTRACTS = os.path.join(HERE, "contracts")
OUT = os.path.join(ROOT, "n8n", "workflows", "TEMP_docusign_templates.json")

# Must match the roleName WF-2 sends in templateRoles, and the four exact
# strings the guard allows. A mismatch here surfaces as a 400 from DocuSign
# with a message about an unmatched role, which is not obviously this file's
# fault, so keep them together.
ROLE_NAME = "Signer"

TEMPLATES = [
    ("setter", "Setter", "Contractor Agreement - Appointment Setter"),
    ("coach", "Coach", "Contractor Agreement - Coach"),
    ("closer", "Closer", "Contractor Agreement - Closer"),
    ("full_cycle", "Full Cycle", "Contractor Agreement - Full Cycle"),
]

NS = uuid.UUID("2f1a6d70-6c3d-4e8a-9a1b-3e5f7c9d0011")


def nid(name):
    return str(uuid.uuid5(NS, name))


def load_pdfs():
    out = {}
    for slug, _role, _title in TEMPLATES:
        path = os.path.join(CONTRACTS, f"{slug}.pdf")
        if not os.path.exists(path):
            raise SystemExit(
                f"missing {path} - run docusign/build_contracts.py first"
            )
        with open(path, "rb") as fh:
            out[slug] = base64.b64encode(fh.read()).decode("ascii")
    return out


def prepare_code(pdfs):
    specs = [
        {
            "slug": slug,
            "role": role,
            "name": title,
            "b64": pdfs[slug],
        }
        for slug, role, title in TEMPLATES
    ]
    return (
        "// One item per template. The PDF travels inline as base64 so the run\n"
        "// needs no file access and can be repeated from a fresh n8n.\n"
        "const info = $('Who am I').first().json;\n"
        "const acct = (info.accounts || []).find(a => a.is_default) "
        "|| (info.accounts || [])[0];\n"
        "if (!acct) throw new Error('userinfo returned no accounts - is the "
        "credential connected to the demo account?');\n"
        "\n"
        "const SPECS = " + json.dumps(specs, indent=2) + ";\n"
        "\n"
        "return SPECS.map(s => ({ json: {\n"
        "  account_id: acct.account_id,\n"
        "  base_uri: acct.base_uri,\n"
        "  role: s.role,\n"
        "  slug: s.slug,\n"
        "  body: {\n"
        "    name: s.name,\n"
        "    emailSubject: 'Please sign your ' + s.role + ' agreement',\n"
        "    emailBlurb: 'This is a demo envelope from an intake pipeline "
        "portfolio project. No real engagement is created by signing it.',\n"
        "    status: 'created',\n"
        "    documents: [{\n"
        "      documentId: '1',\n"
        "      name: s.name,\n"
        "      fileExtension: 'pdf',\n"
        "      documentBase64: s.b64,\n"
        "    }],\n"
        "    recipients: {\n"
        "      signers: [{\n"
        "        recipientId: '1',\n"
        "        roleName: " + json.dumps(ROLE_NAME) + ",\n"
        "        routingOrder: '1',\n"
        "        tabs: {\n"
        "          // Anchored, not positioned. Regenerating the PDF with\n"
        "          // different wording does not move these.\n"
        "          signHereTabs: [{ anchorString: '/sig1/', anchorUnits: "
        "'pixels', anchorXOffset: '0', anchorYOffset: '0' }],\n"
        "          dateSignedTabs: [{ anchorString: '/dt1/', anchorUnits: "
        "'pixels', anchorXOffset: '0', anchorYOffset: '0' }],\n"
        "          fullNameTabs: [{ anchorString: '/nm1/', anchorUnits: "
        "'pixels', anchorXOffset: '0', anchorYOffset: '0' }],\n"
        "        },\n"
        "      }],\n"
        "    },\n"
        "  },\n"
        "} }));\n"
    )


VERIFY_CODE = """
// Template name -> the Config key that must hold its id. Derived here rather
// than reconstructed from the name at runtime, because "Appointment Setter"
// does not spell itself "setter" and a clever regex would silently produce a
// key no Config node reads.
const CONFIG_KEY = __CONFIG_KEY__;

// A 201 only says the template exists. It does not say the anchors were
// found - DocuSign silently keeps a tab it could not place, and the failure
// only shows up as an envelope with nowhere to sign. So read each template
// back and assert the three tabs are there.
const rows = [];
for (const item of $input.all()) {
  const t = item.json;
  const signer = ((t.recipients || {}).signers || [])[0] || {};
  const tabs = signer.tabs || {};
  const counted = {
    signHere: (tabs.signHereTabs || []).length,
    dateSigned: (tabs.dateSignedTabs || []).length,
    fullName: (tabs.fullNameTabs || []).length,
  };
  const ok = counted.signHere === 1 && counted.dateSigned === 1
    && counted.fullName === 1;
  rows.push({
    templateId: t.templateId,
    name: t.name,
    roleName: signer.roleName,
    tabs: counted,
    anchors_placed: ok,
  });
}
const bad = rows.filter(r => !r.anchors_placed);
if (bad.length) {
  throw new Error('anchors missing on: ' + bad.map(b => b.name).join(', ')
    + ' - the PDF text and the anchorString have drifted apart');
}
return [{ json: {
  account_id: $('Prepare templates').first().json.account_id,
  base_uri: $('Prepare templates').first().json.base_uri,
  templates: rows,
  config_values: Object.fromEntries(rows.map(r => [
    CONFIG_KEY[r.name], r.templateId,
  ])),
} }];
"""


def build(pdfs):
    cred = {"oAuth2Api": {"id": "REPLACE_CRED_ID", "name": "DocuSign Sandbox"}}

    nodes = [
        {
            "parameters": {},
            "id": nid("manual"),
            "name": "Manual",
            "type": "n8n-nodes-base.manualTrigger",
            "typeVersion": 1,
            "position": [0, 0],
        },
        {
            "parameters": {
                "url": "https://account-d.docusign.com/oauth/userinfo",
                "options": {},
                "authentication": "genericCredentialType",
                "genericAuthType": "oAuth2Api",
            },
            "id": nid("whoami"),
            "name": "Who am I",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [220, 0],
            "credentials": cred,
            "notes": "Returns the API account id and base URI. Never assume "
                     "these; demo accounts are not all on demo.docusign.net.",
        },
        {
            "parameters": {"jsCode": prepare_code(pdfs)},
            "id": nid("prepare"),
            "name": "Prepare templates",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [440, 0],
        },
        {
            "parameters": {
                "method": "POST",
                "url": "={{ $json.base_uri + '/restapi/v2.1/accounts/' + "
                       "$json.account_id + '/templates' }}",
                "options": {},
                "authentication": "genericCredentialType",
                "genericAuthType": "oAuth2Api",
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ JSON.stringify($json.body) }}",
            },
            "id": nid("create"),
            "name": "Create template",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [660, 0],
            "credentials": cred,
            "executeOnce": False,
        },
        {
            "parameters": {
                "url": "={{ $('Prepare templates').first().json.base_uri + "
                       "'/restapi/v2.1/accounts/' + $('Prepare templates')"
                       ".first().json.account_id + '/templates/' + "
                       "$json.templateId }}",
                "options": {},
                "authentication": "genericCredentialType",
                "genericAuthType": "oAuth2Api",
                "sendQuery": True,
                "queryParameters": {
                    "parameters": [{"name": "include", "value": "recipients"}]
                },
            },
            "id": nid("readback"),
            "name": "Read template back",
            "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2,
            "position": [880, 0],
            "credentials": cred,
        },
        {
            "parameters": {"jsCode": VERIFY_CODE.replace(
                "__CONFIG_KEY__",
                json.dumps(
                    {title: f"template_{slug}" for slug, _r, title in TEMPLATES},
                    indent=2,
                ),
            )},
            "id": nid("verify"),
            "name": "Verify anchors",
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": [1100, 0],
        },
    ]

    chain = ["Manual", "Who am I", "Prepare templates", "Create template",
             "Read template back", "Verify anchors"]
    connections = {
        a: {"main": [[{"node": b, "type": "main", "index": 0}]]}
        for a, b in zip(chain, chain[1:])
    }

    return {
        "name": "TEMP docusign templates",
        "nodes": nodes,
        "connections": connections,
        "settings": {"executionOrder": "v1"},
        "pinData": {},
        "meta": {"templateCredsSetupCompleted": False},
    }


def main():
    pdfs = load_pdfs()
    wf = build(pdfs)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(wf, fh, indent=2)
        fh.write("\n")
    size = os.path.getsize(OUT)
    print(f"{OUT}  ({size:,} bytes, {len(wf['nodes'])} nodes)")
    for slug, role, title in TEMPLATES:
        print(f"  will create: {title:<45} role={role}")


if __name__ == "__main__":
    main()
