use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{
    de::{self, MapAccess, SeqAccess, Visitor},
    Deserialize, Deserializer,
};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fmt,
    sync::OnceLock,
};

pub const MAX_BYTES: usize = 16 * 1024 * 1024;
pub type Result<T> = std::result::Result<T, Error>;
#[derive(Debug, Clone)]
pub struct Error {
    pub code: String,
    pub message: String,
    pub retryable: bool,
}
impl Error {
    pub fn new(code: &str, message: &str) -> Self {
        Self {
            code: code.into(),
            message: message.into(),
            retryable: false,
        }
    }
    pub fn retryable(mut self) -> Self {
        self.retryable = true;
        self
    }
}
pub fn require(ok: bool, code: &str, message: &str) -> Result<()> {
    if ok {
        Ok(())
    } else {
        Err(Error::new(code, message))
    }
}
pub fn arr(v: &Value) -> &[Value] {
    v.as_array().map(Vec::as_slice).unwrap_or(&[])
}
pub fn s(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}
pub fn b(v: &Value) -> bool {
    v.as_bool().unwrap_or(false)
}
pub fn n(v: &Value) -> f64 {
    v.as_f64().unwrap_or(0.0)
}
pub fn u(v: &Value) -> u64 {
    v.as_u64()
        .or_else(|| {
            v.as_f64()
                .filter(|n| {
                    n.is_finite() && *n >= 0.0 && n.fract() == 0.0 && *n <= 9007199254740991.0
                })
                .map(|n| n as u64)
        })
        .unwrap_or(0)
}
pub fn same_scalar(left: &Value, right: &Value) -> bool {
    if left.is_number() && right.is_number() {
        left.as_f64() == right.as_f64()
    } else {
        left == right
    }
}
pub fn strings(v: &Value) -> BTreeSet<String> {
    arr(v).iter().map(|x| s(x).to_owned()).collect()
}
pub fn counter(v: &Value) -> Result<u64> {
    let text = s(v);
    require(
        !text.is_empty()
            && (text == "0" || !text.starts_with('0'))
            && text.bytes().all(|x| x.is_ascii_digit()),
        "INVALID_REQUEST",
        "计数器格式无效",
    )?;
    text.parse()
        .map_err(|_| Error::new("INVALID_REQUEST", "计数器超出 uint64"))
}
pub fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn limits() -> Value {
    json!({"max_request_bytes":MAX_BYTES,"max_response_bytes":MAX_BYTES,"max_tasks":10000,"max_nodes":100000,"max_options":200000,"max_feedback_events":1000,"max_state_bytes":4194304})
}
pub fn reason(code: &str, message: &str, task: Option<&str>) -> Value {
    json!({"code":code,"message":message,"task_id":task,"node":null,"resource_ids":[]})
}
pub fn failure(request: &Value, error: Error) -> Value {
    let id = s(&request["request_id"]);
    let id = uuid::Uuid::parse_str(id)
        .ok()
        .filter(|v| v.to_string() == id)
        .map(|_| id);
    let op = s(&request["operation"]);
    json!({"protocol":"unilab.scheduler","schema_version":"1.0","operation":if ["describe","create","decide","close"].contains(&op) {Some(op)} else {None},"request_id":id,"ok":false,
        "error":{"code":error.code,"message":error.message,"origin":"engine","retryable":error.retryable,"field_path":null,"details":{}}})
}
pub fn success(request: &Value, result: Value) -> Value {
    json!({"protocol":request["protocol"],"schema_version":request["schema_version"],"operation":request["operation"],"request_id":request["request_id"],"ok":true,"result":result})
}
pub fn failure_bytes(raw: &[u8], error: Error) -> Vec<u8> {
    serde_json::to_vec(&failure(&decode(raw).unwrap_or(Value::Null), error)).unwrap()
}

// 不能让重复对象键或不可跨语言精确表示的整数在不同引擎中产生不同含义。
struct Strict(Value);
impl<'de> Deserialize<'de> for Strict {
    fn deserialize<D: Deserializer<'de>>(de: D) -> std::result::Result<Self, D::Error> {
        struct V;
        impl<'de> Visitor<'de> for V {
            type Value = Strict;
            fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
                f.write_str("有限 JSON 值")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> std::result::Result<Strict, E> {
                Ok(Strict(json!(v)))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> std::result::Result<Strict, E> {
                if v.unsigned_abs() > 9007199254740991 {
                    Err(E::custom("整数越界"))
                } else {
                    Ok(Strict(json!(v)))
                }
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> std::result::Result<Strict, E> {
                if v > 9007199254740991 {
                    Err(E::custom("整数越界"))
                } else {
                    Ok(Strict(json!(v)))
                }
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> std::result::Result<Strict, E> {
                if !v.is_finite() {
                    Err(E::custom("非有限数字"))
                } else {
                    Ok(Strict(json!(v)))
                }
            }
            fn visit_str<E: de::Error>(self, v: &str) -> std::result::Result<Strict, E> {
                Ok(Strict(json!(v)))
            }
            fn visit_string<E: de::Error>(self, v: String) -> std::result::Result<Strict, E> {
                Ok(Strict(json!(v)))
            }
            fn visit_unit<E: de::Error>(self) -> std::result::Result<Strict, E> {
                Ok(Strict(Value::Null))
            }
            fn visit_seq<A: SeqAccess<'de>>(
                self,
                mut seq: A,
            ) -> std::result::Result<Strict, A::Error> {
                let mut out = vec![];
                while let Some(Strict(v)) = seq.next_element()? {
                    out.push(v);
                }
                Ok(Strict(Value::Array(out)))
            }
            fn visit_map<A: MapAccess<'de>>(
                self,
                mut map: A,
            ) -> std::result::Result<Strict, A::Error> {
                let mut out = Map::new();
                while let Some((k, Strict(v))) = map.next_entry::<String, Strict>()? {
                    if out.insert(k, v).is_some() {
                        return Err(de::Error::custom("重复 JSON 键"));
                    }
                }
                Ok(Strict(Value::Object(out)))
            }
        }
        de.deserialize_any(V)
    }
}
pub fn decode(raw: &[u8]) -> Result<Value> {
    require(raw.len() <= MAX_BYTES, "LIMIT_EXCEEDED", "消息超过字节上限")?;
    // serde_json 对超 u64 的整数默认转成浮点数；先检查原始数值 token，避免精度丢失。
    let mut i = 0;
    let mut depth: usize = 0;
    let mut negative_integer_zeros = vec![];
    while i < raw.len() {
        match raw[i] {
            b'"' => {
                i += 1;
                while i < raw.len() && raw[i] != b'"' {
                    if raw[i] == b'\\' {
                        i += 1;
                    }
                    i += 1;
                }
                i += 1;
            }
            b'-' | b'0'..=b'9' => {
                let start = i;
                while i < raw.len()
                    && matches!(raw[i], b'0'..=b'9' | b'-' | b'+' | b'.' | b'e' | b'E')
                {
                    i += 1;
                }
                let token = &raw[start..i];
                if token == b"-0" {
                    negative_integer_zeros.push(start);
                }
                if !token.iter().any(|c| matches!(c, b'.' | b'e' | b'E')) {
                    let integer = std::str::from_utf8(token)
                        .ok()
                        .and_then(|v| v.parse::<i64>().ok());
                    require(
                        integer.is_some_and(|v| v.unsigned_abs() <= 9007199254740991),
                        "INVALID_REQUEST",
                        "整数超出跨语言精确范围",
                    )?;
                }
            }
            b'{' | b'[' => {
                depth += 1;
                require(
                    depth <= 1024,
                    "INVALID_REQUEST",
                    "JSON 深度超出宿主解析范围",
                )?;
                i += 1;
            }
            b'}' | b']' => {
                depth = depth.saturating_sub(1);
                i += 1;
            }
            _ => i += 1,
        }
    }
    // Python 将整数 token -0 解释为 0；浮点 -0.0 则保留符号，旧样本指纹依赖此差别。
    let mut normalized;
    let input = if negative_integer_zeros.is_empty() {
        raw
    } else {
        normalized = raw.to_vec();
        for index in negative_integer_zeros {
            normalized[index] = b' ';
        }
        &normalized
    };
    let mut parser = serde_json::Deserializer::from_slice(input);
    parser.disable_recursion_limit();
    let value = Strict::deserialize(serde_stacker::Deserializer::new(&mut parser))
        .map(|x| x.0)
        .map_err(|_| Error::new("INVALID_REQUEST", "消息不是有效 UTF-8 JSON"))?;
    parser
        .end()
        .map_err(|_| Error::new("INVALID_REQUEST", "JSON 含尾随内容"))?;
    Ok(value)
}
pub fn encode(v: &Value, max: usize) -> Result<Vec<u8>> {
    let raw = serde_json::to_vec(v).map_err(|_| Error::new("ENGINE_INTERNAL", "响应编码失败"))?;
    require(raw.len() <= max, "LIMIT_EXCEEDED", "消息超过字节上限")?;
    Ok(raw)
}
pub fn schema() -> &'static Value {
    static VALUE: OnceLock<Value> = OnceLock::new();
    VALUE.get_or_init(|| {
        serde_json::from_str(include_str!(
            "../../../unilabos/scheduling/contract/scheduler-engine-v1.schema.json"
        ))
        .unwrap()
    })
}
pub fn validate(v: &Value, kind: &str, code: &str) -> Result<()> {
    static VALIDATORS: OnceLock<BTreeMap<String, jsonschema::Validator>> = OnceLock::new();
    let validators=VALIDATORS.get_or_init(|| {
        ["DescribeRequest","CreateRequest","DecideRequest","CloseRequest","DescribeResponse","CreateResponse","DecideResponse","CloseResponse","EnginePayload","Observation"]
        .into_iter().map(|name| {let schema=json!({"$schema":schema()["$schema"],"$defs":schema()["$defs"],"$ref":format!("#/$defs/{name}")});(name.to_owned(),jsonschema::options().with_draft(jsonschema::Draft::Draft202012).build(&schema).unwrap())}).collect()
    });
    require(validators[kind].is_valid(v), code, "消息不符合调度引擎协议")
}
pub fn validate_config(v: &Value, schema: &Value) -> Result<()> {
    let validator = jsonschema::options()
        .with_draft(jsonschema::Draft::Draft202012)
        .build(schema)
        .map_err(|_| Error::new("CONFIG_INVALID", "配置 schema 无效"))?;
    require(
        validator.is_valid(v),
        "CONFIG_INVALID",
        "引擎配置不符合 config_schema",
    )
}
pub fn unpack(payload: &Value, formats: &[&str], max: usize) -> Result<Value> {
    validate(payload, "EnginePayload", "STATE_CORRUPT")?;
    require(
        formats.contains(&s(&payload["format"])),
        "STATE_INCOMPATIBLE",
        "不支持检查点格式",
    )?;
    require(
        s(&payload["data_base64"]).len() <= 4 * max.div_ceil(3),
        "LIMIT_EXCEEDED",
        "检查点超过字节上限",
    )?;
    let raw = STANDARD
        .decode(s(&payload["data_base64"]))
        .map_err(|_| Error::new("STATE_CORRUPT", "检查点 Base64 无效"))?;
    require(raw.len() <= max, "LIMIT_EXCEEDED", "检查点超过字节上限")?;
    require(
        digest(&raw) == s(&payload["sha256"]),
        "STATE_CORRUPT",
        "检查点摘要不匹配",
    )?;
    decode(&raw).map_err(|_| Error::new("STATE_CORRUPT", "检查点 JSON 无效"))
}
pub fn pack(v: &Value, format: &str, max: usize) -> Result<Value> {
    let raw = encode(v, max)?;
    Ok(json!({"format":format,"data_base64":STANDARD.encode(&raw),"sha256":digest(&raw)}))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numeric_tokens_keep_the_legacy_json_value_and_fingerprint_semantics() {
        assert_eq!(decode(b"-0").unwrap(), json!(0));
        assert!(decode(b"-0.0")
            .unwrap()
            .as_f64()
            .unwrap()
            .is_sign_negative());
        let exact = b"14.278749999999999";
        assert_eq!(encode(&decode(exact).unwrap(), MAX_BYTES).unwrap(), exact);
        assert_eq!(u(&decode(b"5000.0").unwrap()), 5000);
    }
}
