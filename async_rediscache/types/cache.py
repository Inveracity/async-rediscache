import logging
from typing import Dict, ItemsView, Optional

from .base import RedisKeyType, RedisObject, RedisValueType, namespace_lock

__all__ = [
    "RedisCache",
]

log = logging.getLogger(__name__)


class RedisCache(RedisObject):
    """
    A simplified interface for a Redis hash set.

    We implement several convenient methods that are fairly similar to have a
    dict behaves, and should be familiar to Python users. The biggest difference
    is that all the public methods in this class are coroutines, and must be
    awaited.

    Because of limitations in Redis, this cache will only accept strings and
    integers as keys and strings, integers, floats, and bools as values.

    By default, the namespace key of a RedisCache is automatically determined
    by the name of the owner class and the class attribute assigned to the
    RedisQueue instance. To bind a RedisQueue to a specific namespace, pass the
    namespace as the `namespace` keyword argument to constructor.

    Please note that for automatic namespacing, this class MUST be created as a
    class attribute to properly initialize the instance's namespace. See
    `__set_name__` for more information about how this works.

    Simple example for how to use this:

    class SomeCog(Cog):
        # To initialize a valid RedisCache, just add it as a class attribute
        # here. Do not add it to the __init__ method or anywhere else, it MUST
        # be a class attribute. Do not pass any parameters.
        cache = RedisCache()

        async def my_method(self):

            # Now we're ready to use the RedisCache.
            #
            # We can store some stuff in the cache just by doing this.
            # This data will persist through restarts!
            await self.cache.set("key", "value")

            # To get the data, simply do this.
            value = await self.cache.get("key")

            # Other methods work more or less like a dictionary.
            # Checking if something is in the cache
            await self.cache.contains("key")

            # iterating the cache
            async for key, value in self.cache.items():
                print(value)

            # We can even iterate in a comprehension!
            consumed = [value async for key, value in self.cache.items()]
    """

    def __init__(self, *args, **kwargs) -> None:
        """Initialize the RedisCache."""
        super().__init__(*args, **kwargs)
        self._increment_lock = None

    @namespace_lock
    async def set(self, key: RedisKeyType, value: RedisValueType) -> None:
        """Store an item in the Redis cache."""
        # Convert to a typestring and then set it
        key = self._key_to_typestring(key)
        value = self._value_to_typestring(value)

        log.debug(f"Setting {key} to {value}.")
        with await self._get_pool_connection() as connection:
            await connection.hset(self.namespace, key, value)

    @namespace_lock
    async def get(
            self, key: RedisKeyType, default: Optional[RedisValueType] = None
    ) -> Optional[RedisValueType]:
        """Get an item from the Redis cache."""
        key = self._key_to_typestring(key)

        log.debug(f"Attempting to retrieve {key}.")
        with await self._get_pool_connection() as connection:
            value = await connection.hget(self.namespace, key)

        if value is None:
            log.debug(f"Value not found, returning default value {default}")
            return default
        else:
            value = self._value_from_typestring(value)
            log.debug(f"Value found, returning value {value}")
            return value

    @namespace_lock
    async def delete(self, key: RedisKeyType) -> None:
        """
        Delete an item from the Redis cache.

        If we try to delete a key that does not exist, it will simply be ignored.

        See https://redis.io/commands/hdel for more info on how this works.
        """
        key = self._key_to_typestring(key)

        log.debug(f"Attempting to delete {key}.")
        with await self._get_pool_connection() as connection:
            return await connection.hdel(self.namespace, key)

    @namespace_lock
    async def contains(self, key: RedisKeyType) -> bool:
        """
        Check if a key exists in the Redis cache.

        Return True if the key exists, otherwise False.
        """
        key = self._key_to_typestring(key)
        with await self._get_pool_connection() as connection:
            exists = await connection.hexists(self.namespace, key)

        log.debug(f"Testing if {key} exists in the RedisCache - Result is {exists}")
        return exists

    @namespace_lock
    async def items(self) -> ItemsView:
        """
        Fetch all the key/value pairs in the cache.

        Returns a normal ItemsView, like you would get from dict.items().

        Keep in mind that these items are just a _copy_ of the data in the
        RedisCache - any changes you make to them will not be reflected
        into the RedisCache itself. If you want to change these, you need
        to make a .set call.

        Example:
        items = await my_cache.items()
        for key, value in items:
            # Iterate like a normal dictionary
        """
        with await self._get_pool_connection() as connection:
            items = self._dict_from_typestring(await connection.hgetall(self.namespace)).items()

        log.debug(f"Retrieving all key/value pairs from cache, total of {len(items)} items.")
        return items

    @namespace_lock
    async def length(self) -> int:
        """Return the number of items in the Redis cache."""
        with await self._get_pool_connection() as connection:
            number_of_items = await connection.hlen(self.namespace)
        log.debug(f"Returning length. Result is {number_of_items}.")
        return number_of_items

    @namespace_lock
    async def to_dict(self) -> Dict:
        """Convert to dict and return."""
        return {key: value for key, value in await self.items(acquire_lock=False)}

    @namespace_lock
    async def clear(self) -> None:
        """Deletes the entire hash from the Redis cache."""
        log.debug("Clearing the cache of all key/value pairs.")
        with await self._get_pool_connection() as connection:
            await connection.delete(self.namespace)

    @namespace_lock
    async def pop(
            self, key: RedisKeyType, default: Optional[RedisValueType] = None
    ) -> RedisValueType:
        """Get the item, remove it from the cache, and provide a default if not found."""
        log.debug(f"Attempting to pop {key}.")
        value = await self.get(key, default, acquire_lock=False)

        log.debug(
            f"Attempting to delete item with key '{key}' from the cache. "
            "If this key doesn't exist, nothing will happen."
        )
        await self.delete(key, acquire_lock=False)

        return value

    @namespace_lock
    async def update(self, items: Dict[RedisKeyType, RedisValueType]) -> None:
        """
        Update the Redis cache with multiple values.

        This works exactly like dict.update from a normal dictionary. You pass
        a dictionary with one or more key/value pairs into this method. If the
        keys do not exist in the RedisCache, they are created. If they do exist,
        the values are updated with the new ones from `items`.

        Please note that keys and the values in the `items` dictionary
        must consist of valid RedisKeyTypes and RedisValueTypes.
        """
        log.debug(f"Updating the cache with the following items:\n{items}")
        with await self._get_pool_connection() as connection:
            await connection.hmset_dict(self.namespace, self._dict_to_typestring(items))

    @namespace_lock
    async def increment(self, key: RedisKeyType, amount: Optional[float] = 1) -> None:
        """
        Increment the value by `amount`.

        This works for both floats and ints, but will raise a TypeError
        if you try to do it for any other type of value.

        This also supports negative amounts, although it would provide better
        readability to use .decrement() for that.
        """
        log.debug(f"Attempting to increment/decrement the value with the key {key} by {amount}.")

        value = await self.get(key, acquire_lock=False)

        # Can't increment a non-existing value
        if value is None:
            error_message = "The provided key does not exist!"
            log.error(error_message)
            raise KeyError(error_message)

        # If it does exist and it's an int or a float, increment and set it.
        if isinstance(value, int) or isinstance(value, float):
            value += amount
            await self.set(key, value, acquire_lock=False)
        else:
            error_message = "You may only increment or decrement integers and floats."
            log.error(error_message)
            raise TypeError(error_message)

    async def decrement(self, key: RedisKeyType, amount: Optional[float] = 1) -> None:
        """
        Decrement the value by `amount`.

        Basically just does the opposite of .increment.
        """
        await self.increment(key, -amount)
