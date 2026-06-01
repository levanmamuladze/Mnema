# Legal templates

Draft commercial documents for selling and deploying Mnema.

> ⚠️ **NOT LEGAL ADVICE.** These are starting-point templates, not finished
> contracts. Have a qualified lawyer in your jurisdiction review and adapt every
> one before you send it to a customer or rely on it. Bracketed `[PLACEHOLDERS]`
> must be filled in. Laws differ by country/state; these drafts are generic.

| File | Purpose | When you need it |
|---|---|---|
| [eula.md](eula.md) | End-User License Agreement — the terms under which a customer may use the software. | Every sale. |
| [dpa.md](dpa.md) | Data Processing Agreement — how personal data is handled (GDPR/UK GDPR style). | Customers in/with EU/UK data, or any privacy-mature buyer. |
| [privacy-policy.md](privacy-policy.md) | Privacy notice describing data handling. | Public-facing / when requested by procurement. |

**Note on the self-hosted model:** because Mnema runs on the customer's own
infrastructure and you (the vendor) do not host their data, in most deployments
*the customer is the data controller and also operates the processing
environment*. You are typically a **software supplier**, not a data processor of
their content — which simplifies your DPA position considerably. The DPA draft
covers the limited cases where you do process data (e.g. during paid support).
Confirm this characterization with counsel for each engagement.
