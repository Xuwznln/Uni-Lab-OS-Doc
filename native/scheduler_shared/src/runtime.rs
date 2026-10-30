use crate::{
    protocol::*,
    validation::{validate_decision, validate_snapshot, Facts},
};
use serde_json::{json, Value};
use std::{collections::BTreeMap, time::Instant};

pub trait Algorithm: Default {
    fn engine_id(&self) -> &str;
    fn capabilities(&self) -> Vec<&str> {
        vec!["core.v1", "unilab.resource_bindings.v1"]
    }
    fn config_schema(&self) -> Value;
    fn state_formats(&self) -> Vec<&str> {
        vec![]
    }
    fn ready(&mut self) -> Result<()> {
        Ok(())
    }
    fn check_config(&self, _config: &Value) -> Result<()> {
        Ok(())
    }
    fn check_state(&self, state: Value, _format: &str, _config: &Value) -> Result<Value> {
        Ok(state)
    }
    fn release(&mut self) {}
    fn decide(
        &mut self,
        request: &Value,
        facts: &Facts,
        config: &Value,
        state: Value,
    ) -> Result<(Value, Value)>;
}
#[derive(Clone)]
struct Instance {
    raw: Vec<u8>,
    created: Value,
    body: Value,
    closed: bool,
    cache: BTreeMap<String, (String, String, Value)>,
}
pub struct Runtime<A: Algorithm> {
    algorithm: A,
    build_id: String,
    engine_version: String,
    instances: BTreeMap<String, Instance>,
}
impl<A: Algorithm> Runtime<A> {
    pub fn new(algorithm: A, build_id: &str, engine_version: &str) -> Self {
        Self {
            algorithm,
            build_id: build_id.into(),
            engine_version: engine_version.into(),
            instances: BTreeMap::new(),
        }
    }
    fn checkpoint(&self, checkpoint: &Value, creation: &Value) -> Result<Value> {
        require(
            checkpoint["engine_id"] == self.algorithm.engine_id()
                && checkpoint["config_id"] == creation["config_id"],
            "STATE_INCOMPATIBLE",
            "检查点与引擎或配置不匹配",
        )?;
        counter(&checkpoint["revision"])?;
        counter(&checkpoint["feedback_cursor"])?;
        if checkpoint["state"].is_null() {
            Ok(Value::Null)
        } else {
            let value = unpack(
                &checkpoint["state"],
                &self.algorithm.state_formats(),
                u(&creation["limits"]["max_state_bytes"]) as usize,
            )?;
            self.algorithm.check_state(
                value,
                s(&checkpoint["state"]["format"]),
                &creation["config"],
            )
        }
    }
    fn instance(&self, request: &Value, allow_closed: bool) -> Result<&Instance> {
        let instance = self
            .instances
            .get(s(&request["body"]["instance_id"]))
            .ok_or_else(|| Error::new("INSTANCE_NOT_FOUND", "引擎实例不存在"))?;
        require(
            ["domain_id", "run_epoch"]
                .iter()
                .all(|k| request["body"][k] == instance.body[k]),
            "STATE_INCOMPATIBLE",
            "实例调度域或运行世代不匹配",
        )?;
        require(
            allow_closed || !instance.closed,
            "INSTANCE_CLOSED",
            "实例已关闭",
        )?;
        Ok(instance)
    }
    fn process(&mut self, request: &Value, raw: &[u8]) -> Result<Value> {
        let body = &request["body"];
        match s(&request["operation"]) {
            "describe" => {
                self.algorithm.ready()?;
                require(
                    arr(&body["supported_schema_versions"]).contains(&json!("1.0")),
                    "UNSUPPORTED_SCHEMA",
                    "没有共同协议版本",
                )?;
                require(
                    arr(&body["required_capabilities"])
                        .iter()
                        .all(|v| self.algorithm.capabilities().contains(&s(v))),
                    "UNSUPPORTED_CAPABILITY",
                    "缺少必需能力",
                )?;
                Ok(
                    json!({"engine_id":self.algorithm.engine_id(),"engine_version":self.engine_version,"build_id":self.build_id,"supported_schema_versions":["1.0"],"capabilities":self.algorithm.capabilities(),"config_schema":self.algorithm.config_schema(),"checkpoint_formats":self.algorithm.state_formats(),"limits":limits()}),
                )
            }
            "create" => {
                if let Some(instance) = self.instances.get(s(&body["instance_id"])) {
                    require(!instance.closed, "INSTANCE_CLOSED", "实例已关闭，不能复活")?;
                    require(
                        instance.raw == raw,
                        "IDEMPOTENCY_CONFLICT",
                        "实例身份已经绑定其他创建请求",
                    )?;
                    return Ok(instance.created.clone());
                }
                require(
                    self.instances.values().filter(|i| !i.closed).count() < 1024,
                    "LIMIT_EXCEEDED",
                    "当前引擎实例数量超过上限",
                )?;
                require(
                    arr(&body["required_capabilities"])
                        .iter()
                        .all(|v| self.algorithm.capabilities().contains(&s(v))),
                    "UNSUPPORTED_CAPABILITY",
                    "缺少必需能力",
                )?;
                require(
                    body["limits"]
                        .as_object()
                        .unwrap()
                        .iter()
                        .all(|(k, v)| u(v) <= u(&limits()[k])),
                    "LIMIT_EXCEEDED",
                    "创建配额超过引擎上限",
                )?;
                validate_config(&body["config"], &self.algorithm.config_schema())?;
                self.algorithm.ready()?;
                self.algorithm.check_config(&body["config"])?;
                let restore = &body["restore"];
                counter(&restore["feedback_base_seq"])?;
                let checkpoint = if restore["mode"] == "resume" {
                    require(
                        !restore["checkpoint"].is_null(),
                        "STATE_INCOMPATIBLE",
                        "resume 缺少检查点",
                    )?;
                    self.checkpoint(&restore["checkpoint"], body)?;
                    require(
                        restore["checkpoint"]["feedback_cursor"] == restore["feedback_base_seq"],
                        "FEEDBACK_GAP",
                        "恢复基线与检查点游标不一致",
                    )?;
                    restore["checkpoint"].clone()
                } else {
                    require(
                        restore["checkpoint"].is_null(),
                        "STATE_INCOMPATIBLE",
                        "rebuild 不得携带旧检查点",
                    )?;
                    json!({"revision":"0","feedback_cursor":restore["feedback_base_seq"],"engine_id":self.algorithm.engine_id(),"config_id":body["config_id"],"state":null})
                };
                let result = json!({"instance_id":body["instance_id"],"domain_id":body["domain_id"],"run_epoch":body["run_epoch"],"config_id":body["config_id"],"capabilities":self.algorithm.capabilities(),"limits":body["limits"],"recovery_mode":restore["mode"],"checkpoint":checkpoint,"warnings":[]});
                self.instances.insert(
                    s(&body["instance_id"]).into(),
                    Instance {
                        raw: raw.to_vec(),
                        created: result.clone(),
                        body: body.clone(),
                        closed: false,
                        cache: BTreeMap::new(),
                    },
                );
                Ok(result)
            }
            "decide" => {
                let creation = self.instance(request, false)?.body.clone();
                let facts = validate_snapshot(request, &creation["limits"])?;
                let state = self.checkpoint(&body["checkpoint"], &creation)?;
                let (mut decision, state) =
                    self.algorithm
                        .decide(request, &facts, &creation["config"], state)?;
                let next_state = if state.is_null() {
                    Value::Null
                } else {
                    pack(
                        &state,
                        self.algorithm.state_formats()[0],
                        u(&creation["limits"]["max_state_bytes"]) as usize,
                    )?
                };
                for (key,value) in json!({"decision_id":request["request_id"],"instance_id":body["instance_id"],"domain_id":body["domain_id"],"run_epoch":body["run_epoch"],"base_snapshot_revision":body["snapshot"]["revision"],"base_checkpoint_revision":body["checkpoint"]["revision"],"consumed_feedback_seq":body["feedback"]["through_seq"],"next_state":next_state}).as_object().unwrap(){decision[key]=value.clone();}
                validate_decision(request, &decision, &facts, &self.algorithm.capabilities())?;
                Ok(decision)
            }
            "close" => {
                self.instance(request, true)?;
                let instance = self.instances.get_mut(s(&body["instance_id"])).unwrap();
                instance.closed = true;
                instance.raw.clear();
                instance.created = Value::Null;
                instance
                    .body
                    .as_object_mut()
                    .unwrap()
                    .retain(|k, _| ["domain_id", "run_epoch", "limits"].contains(&k.as_str()));
                instance.cache.retain(|k, _| k == "close");
                if self.instances.values().all(|v| v.closed) {
                    self.algorithm.release();
                }
                Ok(json!({"instance_id":body["instance_id"],"status":"closed"}))
            }
            _ => Err(Error::new("INVALID_REQUEST", "不支持的调度操作")),
        }
    }
    pub fn call(&mut self, operation: &str, raw: &[u8]) -> Vec<u8> {
        let started = Instant::now();
        let mut parsed = Value::Null;
        let result = (|| -> Result<Vec<u8>> {
            parsed = decode(raw)?;
            require(
                parsed["schema_version"] == "1.0",
                "UNSUPPORTED_SCHEMA",
                "不支持调度协议版本",
            )?;
            require(
                parsed["operation"] == operation,
                "INVALID_REQUEST",
                "入口与请求 operation 不一致",
            )?;
            let kind = format!("{}{}", operation[..1].to_uppercase(), &operation[1..]);
            validate(&parsed, &format!("{kind}Request"), "INVALID_REQUEST")?;
            let hash = digest(raw);
            let mut max = MAX_BYTES;
            if ["decide", "close"].contains(&operation) {
                let instance = self.instance(&parsed, operation == "close")?;
                require(
                    raw.len() <= u(&instance.body["limits"]["max_request_bytes"]) as usize,
                    "LIMIT_EXCEEDED",
                    "请求超过协商配额",
                )?;
                max = u(&instance.body["limits"]["max_response_bytes"]) as usize;
                for (id, digest, cached) in instance.cache.values() {
                    if id == s(&parsed["request_id"]) {
                        require(
                            hash == *digest,
                            "IDEMPOTENCY_CONFLICT",
                            "同一请求身份的内容不同",
                        )?;
                        return encode(cached, max);
                    }
                }
            }
            let result = self.process(&parsed, raw)?;
            require(
                started.elapsed().as_millis() <= u(&parsed["timeout_ms"]) as u128,
                "DEADLINE_EXCEEDED",
                "引擎调用超过预算",
            )?;
            let response = success(&parsed, result);
            validate(&response, &format!("{kind}Response"), "ENGINE_INTERNAL")?;
            let wire = encode(&response, max)?;
            if ["decide", "close"].contains(&operation) {
                self.instances
                    .get_mut(s(&parsed["body"]["instance_id"]))
                    .unwrap()
                    .cache
                    .insert(
                        operation.into(),
                        (s(&parsed["request_id"]).into(), hash, response),
                    );
            }
            Ok(wire)
        })();
        result.unwrap_or_else(|error| serde_json::to_vec(&failure(&parsed, error)).unwrap())
    }
}
