from __future__ import annotations

import asyncio

from pydantic import BaseModel

from kama_claude.core.events.bus import EventBus


class _FakeEvent(BaseModel):
    value: str


class _RunEvent(BaseModel):
    run_id: str
    value: str


# 功能：验证 publish 后订阅者能收到事件对象
# 设计：用内联 handler 收集事件引用，断言 is 而非 ==，排除序列化中间步骤的干扰
async def test_publish_reaches_subscriber() -> None:
    bus = EventBus()
    received: list[BaseModel] = []

    async def handler(event: BaseModel) -> None:
        received.append(event)

    bus.subscribe(handler)
    event = _FakeEvent(value="hello")
    await bus.publish(event)
    assert received == [event]


# 功能：验证多个订阅者都能独立收到同一事件
# 设计：两个独立计数器分别累加，避免共享状态掩盖某一订阅者未被调用的情况
async def test_multiple_subscribers_all_receive() -> None:
    bus = EventBus()
    counts = [0, 0]

    async def h1(e: BaseModel) -> None:
        counts[0] += 1

    async def h2(e: BaseModel) -> None:
        counts[1] += 1

    bus.subscribe(h1)
    bus.subscribe(h2)
    await bus.publish(_FakeEvent(value="x"))
    assert counts == [1, 1]


# 功能：验证多个订阅者按注册顺序被依次调用
# 设计：用追加整数到列表来记录调用次序，因为 bus 的顺序语义是 AgentLoop 事件序列正确性的前提
async def test_subscribers_called_in_order() -> None:
    bus = EventBus()
    order: list[int] = []

    async def h1(e: BaseModel) -> None:
        order.append(1)

    async def h2(e: BaseModel) -> None:
        order.append(2)

    bus.subscribe(h1)
    bus.subscribe(h2)
    await bus.publish(_FakeEvent(value="x"))
    assert order == [1, 2]


# 功能：验证无订阅者时 publish 不抛异常（空 bus 边界条件）
# 设计：只调用 publish，不断言返回值，以"不引发异常"作为唯一判据
async def test_no_subscribers_publish_is_noop() -> None:
    bus = EventBus()
    await bus.publish(_FakeEvent(value="x"))  # should not raise


# 功能：验证 subscribe 返回的句柄可幂等退订，退订后不再接收事件
# 设计：连续调用两次 unsubscribe 后再 publish，断言处理器未执行，覆盖重复清理的生命周期边界
async def test_subscription_handle_unsubscribes_idempotently() -> None:
    bus = EventBus()
    received: list[BaseModel] = []

    async def handler(event: BaseModel) -> None:
        received.append(event)

    subscription = bus.subscribe(handler)
    subscription.unsubscribe()
    subscription.unsubscribe()

    await bus.publish(_FakeEvent(value="ignored"))

    assert subscription.active is False
    assert received == []


# 功能：验证带 run_id 的订阅只接收目标 run，不接收其他 run 或无 run_id 事件
# 设计：依次发布目标、其他和无 scope 三类事件，精确断言仅目标事件进入列表，证明过滤发生在总线边界
async def test_run_scoped_subscription_filters_other_runs() -> None:
    bus = EventBus()
    received: list[BaseModel] = []

    async def handler(event: BaseModel) -> None:
        received.append(event)

    bus.subscribe(handler, run_id="run-a")
    target = _RunEvent(run_id="run-a", value="target")
    await bus.publish(target)
    await bus.publish(_RunEvent(run_id="run-b", value="other"))
    await bus.publish(_FakeEvent(value="global"))

    assert received == [target]


# 功能：验证慢处理器执行期间退订不会取消在途调用，但会阻止后续事件继续进入
# 设计：用两个 asyncio.Event 精确控制慢处理器停顿点，避免 sleep 竞态并验证快照遍历下的安全退订语义
async def test_unsubscribe_while_slow_handler_is_running_stops_future_delivery() -> None:
    bus = EventBus()
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def slow_handler(event: BaseModel) -> None:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()

    subscription = bus.subscribe(slow_handler)
    publish_task = asyncio.create_task(bus.publish(_FakeEvent(value="first")))
    await entered.wait()

    subscription.unsubscribe()
    release.set()
    await publish_task
    await bus.publish(_FakeEvent(value="second"))

    assert calls == 1
