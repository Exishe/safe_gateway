// Entity types as the engine names them (gateway/engine/types.py, EntityType).
// tests/unit/test_web_entities.py checks that this list matches the Python enum.
export const ENTITY_TYPES = [
  "PERSON",
  "ORG",
  "LOCATION",
  "ADDRESS",
  "EMAIL",
  "PHONE",
  "DATE",
  "DATE_OF_BIRTH",
  "SSN",
  "PASSPORT",
  "DRIVER_LICENSE",
  "NATIONAL_ID",
  "CREDIT_CARD",
  "IBAN",
  "BANK_ACCOUNT",
  "IP_ADDRESS",
  "MEDICAL_RECORD_NUMBER",
  "HEALTH_CONDITION",
  "CASE_NUMBER",
  "URL_WITH_TOKEN",
  "CODENAME",
  "LITERAL",
] as const;

export type EntityType = (typeof ENTITY_TYPES)[number];

// Families share a highlight colour. The type's name is always shown too, so colour
// never carries the meaning on its own.
export type Family = "identity" | "contact" | "money" | "health" | "matter" | "literal";

const FAMILY: Record<EntityType, Family> = {
  PERSON: "identity",
  DATE_OF_BIRTH: "identity",
  SSN: "identity",
  PASSPORT: "identity",
  DRIVER_LICENSE: "identity",
  NATIONAL_ID: "identity",
  EMAIL: "contact",
  PHONE: "contact",
  ADDRESS: "contact",
  LOCATION: "contact",
  IP_ADDRESS: "contact",
  URL_WITH_TOKEN: "contact",
  CREDIT_CARD: "money",
  IBAN: "money",
  BANK_ACCOUNT: "money",
  MEDICAL_RECORD_NUMBER: "health",
  HEALTH_CONDITION: "health",
  ORG: "matter",
  CASE_NUMBER: "matter",
  CODENAME: "matter",
  DATE: "matter",
  LITERAL: "literal",
};

const LABEL: Record<EntityType, string> = {
  PERSON: "Name",
  ORG: "Organisation",
  LOCATION: "Place",
  ADDRESS: "Address",
  EMAIL: "Email",
  PHONE: "Phone",
  DATE: "Date",
  DATE_OF_BIRTH: "Date of birth",
  SSN: "Social Security number",
  PASSPORT: "Passport",
  DRIVER_LICENSE: "Driving licence",
  NATIONAL_ID: "National ID",
  CREDIT_CARD: "Card number",
  IBAN: "IBAN",
  BANK_ACCOUNT: "Bank account",
  IP_ADDRESS: "IP address",
  MEDICAL_RECORD_NUMBER: "Medical record number",
  HEALTH_CONDITION: "Health detail",
  CASE_NUMBER: "Case number",
  URL_WITH_TOKEN: "Link with a token",
  CODENAME: "Listed name",
  LITERAL: "Placeholder-shaped text",
};

export function isEntityType(value: string): value is EntityType {
  return (ENTITY_TYPES as readonly string[]).includes(value);
}

export function familyOf(type: string): Family {
  return isEntityType(type) ? FAMILY[type] : "matter";
}

export function labelOf(type: string): string {
  return isEntityType(type) ? LABEL[type] : type;
}
