from __future__ import annotations

from collections.abc import Awaitable, Callable

from pydantic import BaseModel

type EventHandler = Callable[[BaseModel], Awaitable[None]]


class EventSubscription:
    # 保存一次 EventBus 订阅的处理器、run 过滤条件和活动状态
    def __init__(
        self,
        bus: EventBus,
        handler: EventHandler,
        run_id: str | None,
    ) -> None:
        self._bus = bus
        self.handler = handler
        self.run_id = run_id
        self.active = True

    # 幂等地解除当前订阅，后续 publish 不再调用处理器
    def unsubscribe(self) -> None:
        self._bus.unsubscribe(self)


class EventBus:
    # 初始化空订阅表；订阅对象本身充当可退订句柄
    def __init__(self) -> None:
        self._subscribers: list[EventSubscription] = []

    # 注册事件处理函数，可选只接收指定 run_id，并返回可幂等退订的句柄
    def subscribe(
        self,
        handler: EventHandler,
        *,
        run_id: str | None = None,
    ) -> EventSubscription:
        subscription = EventSubscription(self, handler, run_id)
        self._subscribers.append(subscription)
        return subscription

    # 从总线移除指定订阅；重复调用或传入已移除句柄均安全
    def unsubscribe(self, subscription: EventSubscription) -> None:
        if not subscription.active:
            return
        subscription.active = False
        self._subscribers = [item for item in self._subscribers if item is not subscription]

    # 按注册顺序调用活动且 scope 匹配的订阅者；快照遍历允许处理期间退订
    async def publish(self, event: BaseModel) -> None:
        event_run_id = getattr(event, "run_id", None)
        for subscription in list(self._subscribers):
            if not subscription.active:
                continue
            if subscription.run_id is not None and subscription.run_id != event_run_id:
                continue
            await subscription.handler(event)
