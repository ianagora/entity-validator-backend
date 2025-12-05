# schema.py
SCHEMA_VERSION = "2025-08-29"

# EXACT 26 headers (entity-level) — unchanged strings
SCHEMA_ENTITY_FIELDS = [
    "Customer_id",
    "Entity_type",
    "Entity_name",
    "Entity_trading_name",
    "Entity_registration_number",
    "Entity_incorporation_date",
    "Entity_status (active/dissolved etc)",
    "Entity_primary_address_line1",
    "Entity_primary_address_line2",
    "Entity_primary_city",
    "Entity_primary_postcode",
    "Entity_primary_country",
    "Entity_primary_phone",
    "Entity_primary_email",
    "Entity_Industry_sector",
    "Existing_SIC_codes",
    "Entity_nature_&_purpose",
    "Existing_accounts_balance",
    "Expected_annual_revenue",
    "Expected_money_into_account",
    "Expected_money_out_of_account",
    "Expected_revenue_sources",
    "Expected_transaction_jurisdictions",
    "Products_held",
    "Source_Of_Funds",
    "Source_Of_Wealth",
]

# Linked-party repeated block definition
LP_PREFIX = {
    "full_name":              "Linked_party_full_name_",
    "role":                   "Linked_party_role_",
    "dob":                    "Linked_party_DoB_",
    "nationality":            "Linked_party_nationality_",
    "country_of_residence":   "Linked_party_Country_of_residence_",
    "correspondence_address": "Linked_party_correspondence_address_",
    "appointed_on":           "Linked_party_appointed_on_",
    "pep_rca_status":         "Linked_party_PEP_&_RCA_Status_",
    "sanction_status":        "Linked_party_Sanction_Status_",
    "adverse_media_status":   "Linked_party_Adverse_Media_Status_",
}
LP_COUNT = 50