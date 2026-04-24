package com.example

interface Processor {
    fun process()
}

open class BaseProcessor : Processor {
    override fun process() {
        println("Base processing")
    }
}
