package com.example
import org.apache.kafka.streams.StreamsBuilder
import org.apache.kafka.streams.kstream.KStream

class UserProcessor {
    fun process(builder: StreamsBuilder) {
        val stream: KStream<String, String> = builder.stream("users-topic")
        stream.to("processed-users")
    }
}
