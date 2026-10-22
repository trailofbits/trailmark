#![allow(dead_code)]
// SAFETY: These probe symbols are unique in this isolated test library.
#[unsafe(no_mangle)]
extern "C" fn hidden_export() {}
#[unsafe(no_mangle)]
pub fn rust_abi_export() {}
#[unsafe(export_name = "audit_export")]
extern "C" fn renamed_export() {}
pub extern "C" fn signature_control() {}
fn ordinary() {}
